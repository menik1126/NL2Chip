"""
Sparkle Evaluator — compile, extract Verilog, lint, simulate, synthesize, P&R, DRC, LVS.

Given a prob_id, runs the full evaluation pipeline:
  1. lake build Generated.<prob_id>  → check compilation
  2. Extract SystemVerilog from build output
  3. iverilog lint check
  4. iverilog simulation against VerilogEval testbench (with TopModule wrapper)
  5. (optional) Yosys synthesis via ORFS Docker → PPA extraction
  6. (optional) Full P&R (floorplan → place → CTS → route → finish) → GDS
  7. (optional) DRC via KLayout
  8. (optional) LVS via KLayout (best-effort)

Returns a score dict for each problem.
"""
from __future__ import annotations

import math
import re
import shutil
import subprocess
import sys
from pathlib import Path


DATASET_DIR = Path("verilog-eval/dataset_spec-to-rtl")
BASH_TIMEOUT = 120
SIM_TIMEOUT = 30
SYNTH_TIMEOUT = 600
PNR_TIMEOUT = 900
DRC_TIMEOUT = 300
LVS_TIMEOUT = 300
MIN_DIE_SIDE_UM = 50  # minimum die side for sky130hd PDN straps


# ── Port parsing (from autoformalize.py) ─────────────────────────


def parse_module_ports(sv_code: str) -> tuple[str, list[tuple[str, str, str]]]:
    """Parse a SystemVerilog module to get name and ports.
    Returns (module_name, [(direction, type, name), ...])
    """
    m = re.search(r"module\s+(\w+)\s*\(", sv_code)
    if not m:
        return "", []
    mod_name = m.group(1)
    ports = []
    for pm in re.finditer(
        r"(input|output)\s+(logic(?:\s*\[\d+:\d+\])?)\s+(\w+)", sv_code
    ):
        direction, typ, name = pm.group(1), pm.group(2), pm.group(3)
        ports.append((direction, typ, name))
    return mod_name, ports


def _port_width(typ: str) -> int:
    """Extract bit width from a type like 'logic [7:0]' or 'logic'."""
    m = re.search(r"\[(\d+):(\d+)\]", typ)
    if m:
        return abs(int(m.group(1)) - int(m.group(2))) + 1
    return 1


def parse_ref_ports(ref_sv: str) -> list[tuple[str, str, str]]:
    """Parse RefModule ports from reference Verilog."""
    ports = []
    for m in re.finditer(
        r"(input|output)\s+(?:reg\s+|logic\s+|wire\s+)?(\[[\d:]+\])?\s*(\w+)",
        ref_sv,
    ):
        direction = m.group(1)
        width = m.group(2) or ""
        name = m.group(3)
        typ = f"logic {width}".strip() if width else "logic"
        ports.append((direction, typ, name))
    return ports


def generate_top_wrapper(
    sparkle_mod_name: str,
    sparkle_ports: list[tuple[str, str, str]],
    ref_ports: list[tuple[str, str, str]],
) -> str | None:
    """Generate a TopModule wrapper mapping ref ports to sparkle ports."""
    ref_inputs = [(d, t, n) for d, t, n in ref_ports if d == "input"]
    ref_outputs = [(d, t, n) for d, t, n in ref_ports if d == "output"]
    sp_inputs = [(d, t, n) for d, t, n in sparkle_ports if d == "input"]
    sp_outputs = [(d, t, n) for d, t, n in sparkle_ports if d == "output"]

    sp_user_inputs = [(d, t, n) for d, t, n in sp_inputs if n not in ("clk", "rst")]
    ref_user_inputs = [(d, t, n) for d, t, n in ref_inputs if n != "clk"]

    input_map: dict[str, str] = {}
    for i, (_, _, rn) in enumerate(ref_user_inputs):
        if i < len(sp_user_inputs):
            input_map[rn] = sp_user_inputs[i][2]
        else:
            for _, _, sn in sp_user_inputs:
                if sn == rn or sn == f"_gen_{rn}":
                    input_map[rn] = sn
                    break

    output_map: dict[str, str] = {}
    bundled_output = False
    if len(ref_outputs) > len(sp_outputs) == 1:
        # Sparkle bundled multiple outputs into one port — check widths match
        sp_width = _port_width(sp_outputs[0][1])
        ref_total = sum(_port_width(t) for _, t, _ in ref_outputs)
        if sp_width == ref_total:
            bundled_output = True
    if not bundled_output:
        for i, (_, _, rn) in enumerate(ref_outputs):
            if i < len(sp_outputs):
                output_map[rn] = sp_outputs[i][2]
            else:
                for _, _, sn in sp_outputs:
                    if sn == rn or sn == f"_gen_{rn}":
                        output_map[rn] = sn
                        break

    lines = ["module TopModule ("]
    all_ref_ports = ref_inputs + ref_outputs
    port_decls = []
    for d, t, n in all_ref_ports:
        width = ""
        wm = re.search(r"\[(\d+):(\d+)\]", t)
        if wm:
            width = f" [{wm.group(1)}:{wm.group(2)}]"
        port_decls.append(f"    {d}{width} {n}")
    lines.append(",\n".join(port_decls))
    lines.append(");")
    lines.append("")

    for _, t, n in sp_outputs:
        lines.append(f"    {t} {n}_wire;")

    lines.append(f"    {sparkle_mod_name} dut (")
    inst_conns = []

    for _, _, sn in sp_inputs:
        if sn == "clk":
            inst_conns.append(f"        .clk(clk)")
        elif sn == "rst":
            if "areset" in input_map:
                inst_conns.append(f"        .rst(areset)")
            elif "reset" in input_map:
                inst_conns.append(f"        .rst(reset)")
            else:
                inst_conns.append(f"        .rst(1'b0)")
        else:
            matched = False
            for rn, sn2 in input_map.items():
                if sn2 == sn:
                    inst_conns.append(f"        .{sn}({rn})")
                    matched = True
                    break
            if not matched:
                inst_conns.append(f"        .{sn}({sn})")

    for _, _, sn in sp_outputs:
        inst_conns.append(f"        .{sn}({sn}_wire)")

    lines.append(",\n".join(inst_conns))
    lines.append("    );")

    if bundled_output:
        # Bit-slice the single bundled output: first ref output = MSB
        sp_out_name = sp_outputs[0][2]
        offset = _port_width(sp_outputs[0][1])
        for _, rt, rn in ref_outputs:
            w = _port_width(rt)
            high = offset - 1
            low = offset - w
            if w == 1:
                lines.append(f"    assign {rn} = {sp_out_name}_wire[{low}];")
            else:
                lines.append(f"    assign {rn} = {sp_out_name}_wire[{high}:{low}];")
            offset -= w
    else:
        for rn, sn in output_map.items():
            lines.append(f"    assign {rn} = {sn}_wire;")

    lines.append("endmodule")
    return "\n".join(lines)


# ── Evaluator ────────────────────────────────────────────────────


class Evaluator:
    """Evaluate a Sparkle-generated .lean file: compile → extract SV → lint → sim → (synth+PPA) → (P&R+DRC+LVS)."""

    def __init__(self, project_root: Path = Path("."), enable_synth: bool = False, enable_pnr: bool = False, enable_drc: bool = False, enable_lvs: bool = False):
        self.project_root = project_root.resolve()
        self.dataset_dir = self.project_root / "verilog-eval" / "dataset_spec-to-rtl"
        self.enable_pnr = enable_pnr or enable_drc or enable_lvs  # drc/lvs imply pnr
        self.enable_synth = enable_synth or self.enable_pnr  # pnr implies synth
        self.enable_drc = enable_drc
        self.enable_lvs = enable_lvs

        if self.enable_synth:
            # Add siliconcrew/src to path for synthesis tools
            sc_src = self.project_root / "siliconcrew" / "src"
            if str(sc_src) not in sys.path:
                sys.path.insert(0, str(sc_src))

    def evaluate(self, prob_id: str, run_dir: Path) -> dict:
        """Run full evaluation for a single problem.

        Args:
            prob_id: e.g. "Prob001_zero"
            run_dir: directory for storing sim artifacts

        Returns dict with keys:
            compile_pass, sv_extracted, lint_pass, sim_status, sim_mismatches, detail
        """
        result = {
            "prob_id": prob_id,
            "compile_pass": False,
            "sv_extracted": False,
            "lint_pass": False,
            "sim_status": "not_run",
            "sim_mismatches": -1,
            "synth_pass": False,
            "area_um2": None,
            "cell_count": None,
            "wns_ns": None,
            "power_uw": None,
            "pnr_pass": False,
            "gds_generated": False,
            "drc_pass": None,        # None = not run, True/False = result
            "drc_violations": None,
            "lvs_pass": None,        # None = not run, True/False = result
            "lvs_error": None,
            "detail": "",
        }

        # 1. Compile
        lean_file = self.project_root / "Generated" / f"{prob_id}.lean"
        if not lean_file.exists():
            result["detail"] = f"Lean file not found: Generated/{prob_id}.lean"
            return result

        try:
            comp = subprocess.run(
                ["lake", "build", f"Generated.{prob_id}"],
                capture_output=True, text=True,
                timeout=BASH_TIMEOUT,
                cwd=str(self.project_root),
            )
        except subprocess.TimeoutExpired:
            result["detail"] = "lake build timeout"
            return result

        build_output = comp.stdout + "\n" + comp.stderr
        has_error = comp.returncode != 0 or re.search(r"error:", build_output)
        if has_error:
            result["detail"] = f"Compile failed:\n{build_output[:1000]}"
            return result

        result["compile_pass"] = True

        # 2. Extract SystemVerilog
        sv_code = self._extract_sv(build_output)
        if not sv_code:
            result["detail"] = "Compiled but could not extract SystemVerilog"
            return result

        result["sv_extracted"] = True
        sv_dir = run_dir / "sv"
        sv_dir.mkdir(parents=True, exist_ok=True)
        sv_file = sv_dir / f"{prob_id}.sv"
        sv_file.write_text(sv_code)

        # 3. Lint
        result["lint_pass"] = self._run_lint(sv_file)

        # Parse module name (needed for sim wrapper and synthesis)
        sparkle_mod_name, sparkle_ports = parse_module_ports(sv_code)

        # 4. Simulation
        sim_status, mismatches, detail = self._run_sim(
            prob_id, sv_code, sparkle_mod_name, sparkle_ports, run_dir
        )
        result["sim_status"] = sim_status
        result["sim_mismatches"] = mismatches
        result["detail"] = detail

        # 5. Synthesis + PPA (optional)
        if self.enable_synth and result["sv_extracted"]:
            synth_result = self._run_synthesis(
                prob_id, sv_code, sparkle_mod_name or prob_id.lower(), run_dir
            )
            result.update(synth_result)

            # 6. Full P&R + DRC + LVS (optional)
            if self.enable_pnr and result["synth_pass"]:
                pnr_result = self._run_pnr(
                    prob_id, sv_code, sparkle_mod_name or prob_id.lower(), run_dir
                )
                result.update(pnr_result)

        return result

    @staticmethod
    def _extract_sv(build_output: str) -> str:
        """Extract SystemVerilog from lake build output, stripping info: prefix."""
        lines = build_output.splitlines()
        sv_lines = []
        capturing = False
        for line in lines:
            # Strip "info: Generated/Prob001_zero.lean:12:0: " prefix
            cleaned = re.sub(r"^info:\s+\S+\s+", "", line)
            if "// Generated by Sparkle HDL" in cleaned:
                capturing = True
            if capturing:
                sv_lines.append(cleaned)
            if capturing and cleaned.strip() == "endmodule":
                break
        return "\n".join(sv_lines) if sv_lines else ""

    @staticmethod
    def _run_lint(sv_file: Path) -> bool:
        """Run iverilog lint check."""
        try:
            comp = subprocess.run(
                ["iverilog", "-t", "null", "-g2012", str(sv_file)],
                capture_output=True, text=True, timeout=30,
            )
            return comp.returncode == 0 and not comp.stderr.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _run_sim(
        self, prob_id: str, sv_code: str,
        sparkle_mod_name: str, sparkle_ports: list[tuple[str, str, str]],
        run_dir: Path,
    ) -> tuple[str, int, str]:
        """Run iverilog simulation against VerilogEval testbench."""
        ref_sv_path = self.dataset_dir / f"{prob_id}_ref.sv"
        test_sv_path = self.dataset_dir / f"{prob_id}_test.sv"

        if not ref_sv_path.exists() or not test_sv_path.exists():
            return "sim_error", -1, f"Missing ref or test SV for {prob_id}"

        ref_sv = ref_sv_path.read_text()

        # Generate wrapper
        ref_ports = parse_ref_ports(ref_sv)

        if not sparkle_mod_name:
            return "sim_error", -1, "Could not parse Sparkle module name"

        wrapper = generate_top_wrapper(sparkle_mod_name, sparkle_ports, ref_ports)
        if not wrapper:
            return "sim_error", -1, "Could not generate TopModule wrapper"

        # Write sim files
        sim_dir = run_dir / "sim" / prob_id
        sim_dir.mkdir(parents=True, exist_ok=True)
        (sim_dir / "sparkle_dut.sv").write_text(sv_code)
        (sim_dir / "wrapper.sv").write_text(wrapper)

        # Compile
        try:
            comp = subprocess.run(
                [
                    "iverilog", "-g2012", "-o", str(sim_dir / "sim.vvp"),
                    str(ref_sv_path),
                    str(sim_dir / "sparkle_dut.sv"),
                    str(sim_dir / "wrapper.sv"),
                    str(test_sv_path),
                ],
                capture_output=True, text=True, timeout=30,
            )
            if comp.returncode != 0:
                (sim_dir / "compile_error.txt").write_text(comp.stderr)
                return "sim_error", -1, f"iverilog compile failed:\n{comp.stderr[:500]}"
        except subprocess.TimeoutExpired:
            return "sim_error", -1, "iverilog compile timeout"
        except FileNotFoundError:
            return "sim_error", -1, "iverilog not found"

        # Run simulation
        try:
            sim = subprocess.run(
                ["vvp", str(sim_dir / "sim.vvp")],
                capture_output=True, text=True, timeout=SIM_TIMEOUT,
            )
            sim_output = sim.stdout + sim.stderr
            (sim_dir / "sim_output.txt").write_text(sim_output)
        except subprocess.TimeoutExpired:
            return "sim_error", -1, "Simulation timeout"

        # Parse results
        m = re.search(r"Mismatches:\s*(\d+)\s+in\s+(\d+)\s+samples", sim_output)
        if m:
            mismatches = int(m.group(1))
            total = int(m.group(2))
            if mismatches == 0:
                return "sim_pass", 0, f"0 mismatches in {total} samples"
            else:
                return "sim_fail", mismatches, f"{mismatches} mismatches in {total} samples"

        if "TIMEOUT" in sim_output:
            return "sim_error", -1, "Simulation hit internal timeout"

        return "sim_error", -1, f"Could not parse sim output:\n{sim_output[:300]}"

    def _run_synthesis(
        self, prob_id: str, sv_code: str, top_module: str, run_dir: Path
    ) -> dict:
        """Run Yosys synthesis via ORFS Docker and extract PPA metrics."""
        result: dict = {
            "synth_pass": False,
            "area_um2": None,
            "cell_count": None,
            "wns_ns": None,
            "power_uw": None,
        }

        try:
            from tools.run_docker import run_docker_command
        except ImportError as e:
            result["synth_error"] = f"Cannot import siliconcrew tools: {e}"
            return result

        # Set up workspace directory
        synth_dir = run_dir / "synth" / prob_id
        synth_dir.mkdir(parents=True, exist_ok=True)

        # Write SV to workspace
        sv_file = synth_dir / f"{prob_id}.sv"
        sv_file.write_text(sv_code)

        # Check if module has a clk port — if not, write an empty SDC
        # (ORFS defaults to `create_clock [get_ports clk]` which errors on combinational designs)
        has_clk = bool(re.search(r'\binput\b.*\bclk\b', sv_code))
        sdc_file = synth_dir / "constraints.sdc"
        if not has_clk:
            sdc_file.write_text("# Combinational design — no clock constraint\n")
        else:
            sdc_file.write_text("create_clock -period 10 [get_ports clk]\n")

        # Generate config.mk
        container_sv = f"/workspace/{prob_id}.sv"
        config_content = (
            f"export DESIGN_NAME = {top_module}\n"
            f"export PLATFORM = sky130hd\n"
            f"export VERILOG_FILES = {container_sv}\n"
            f"export SDC_FILE = /workspace/constraints.sdc\n"
            f"export CORE_UTILIZATION = 5\n"
            f"export CORE_ASPECT_RATIO = 1\n"
            f"export CORE_MARGIN = 2\n"
        )
        (synth_dir / "config.mk").write_text(config_content)

        # Set up output directories
        results_dir = synth_dir / "orfs_results"
        logs_dir = synth_dir / "orfs_logs"
        reports_dir = synth_dir / "orfs_reports"
        for d in [results_dir, logs_dir, reports_dir]:
            d.mkdir(parents=True, exist_ok=True)

        volumes = [
            f"{results_dir}:/OpenROAD-flow-scripts/flow/results",
            f"{logs_dir}:/OpenROAD-flow-scripts/flow/logs",
            f"{reports_dir}:/OpenROAD-flow-scripts/flow/reports",
        ]

        # Run synthesis only (not the full flow)
        make_cmd = "make -B DESIGN_CONFIG=/workspace/config.mk synth"

        # Run synthesis
        try:
            synth_result = run_docker_command(
                command=make_cmd,
                workspace_path=str(synth_dir),
                volumes=volumes,
                timeout=SYNTH_TIMEOUT,
            )
        except Exception as e:
            result["synth_error"] = f"Synthesis exception: {e}"
            return result

        if not synth_result.get("success"):
            stderr = synth_result.get("stderr", "")[:500]
            result["synth_error"] = f"Synthesis failed: {stderr}"
            # Save logs even on failure
            (synth_dir / "synth_stdout.txt").write_text(
                synth_result.get("stdout", "")
            )
            (synth_dir / "synth_stderr.txt").write_text(
                synth_result.get("stderr", "")
            )
            return result

        result["synth_pass"] = True

        # Save synthesis logs
        (synth_dir / "synth_stdout.txt").write_text(
            synth_result.get("stdout", "")
        )

        # Extract PPA metrics from synth_stat.txt
        try:
            stat_files = list((synth_dir / "orfs_reports").rglob("synth_stat.txt"))
            for sf in stat_files:
                content = sf.read_text()
                # Area: "Chip area for module '\\name': 11.260800"
                m_area = re.search(r"Chip area.*?:\s*([0-9.]+)", content)
                if m_area and result["area_um2"] is None:
                    result["area_um2"] = float(m_area.group(1))
                # Cell count: "2   11.261 cells"
                m_cells = re.search(r"(\d+)\s+[\d.]+\s+cells", content)
                if m_cells and result["cell_count"] is None:
                    result["cell_count"] = int(m_cells.group(1))
        except Exception as e:
            result["ppa_error"] = f"PPA extraction failed: {e}"

        return result

    def _run_pnr(
        self, prob_id: str, sv_code: str, top_module: str, run_dir: Path
    ) -> dict:
        """Run full P&R (floorplan→place→CTS→route→finish) + DRC + LVS."""
        from tools.run_docker import run_docker_command

        result: dict = {
            "pnr_pass": False,
            "gds_generated": False,
            "drc_pass": None,
            "drc_violations": None,
            "lvs_pass": None,
            "lvs_error": None,
        }

        synth_dir = run_dir / "synth" / prob_id
        if not synth_dir.exists():
            result["pnr_error"] = "Synth directory not found"
            return result

        # ── Calculate DIE_AREA from synth cell area ──
        cell_area = None
        try:
            for sf in (synth_dir / "orfs_reports").rglob("synth_stat.txt"):
                m = re.search(r"Chip area.*?:\s*([0-9.]+)", sf.read_text())
                if m:
                    cell_area = float(m.group(1))
                    break
        except Exception:
            pass

        if cell_area and cell_area > 0:
            # Target ~30% utilization, with minimum die size for PDN
            core_side = math.sqrt(cell_area / 0.3)
            die_side = max(core_side + 4, MIN_DIE_SIDE_UM)
        else:
            die_side = MIN_DIE_SIDE_UM

        die_side = math.ceil(die_side)
        margin = 2
        core_side = die_side - 2 * margin

        # ── Update config.mk with DIE_AREA ──
        has_clk = bool(re.search(r'\binput\b.*\bclk\b', sv_code))
        container_sv = f"/workspace/{prob_id}.sv"
        config_content = (
            f"export DESIGN_NAME = {top_module}\n"
            f"export PLATFORM = sky130hd\n"
            f"export VERILOG_FILES = {container_sv}\n"
            f"export SDC_FILE = /workspace/constraints.sdc\n"
            f"export DIE_AREA = 0 0 {die_side} {die_side}\n"
            f"export CORE_AREA = {margin} {margin} {core_side} {core_side}\n"
            f"export PLACE_DENSITY = 0.15\n"
        )
        (synth_dir / "config.mk").write_text(config_content)

        # Ensure SDC exists (should already from synth phase)
        sdc_file = synth_dir / "constraints.sdc"
        if not sdc_file.exists():
            if not has_clk:
                sdc_file.write_text("# Combinational design\n")
            else:
                sdc_file.write_text("create_clock -period 10 [get_ports clk]\n")

        volumes = [
            f"{synth_dir / 'orfs_results'}:/OpenROAD-flow-scripts/flow/results",
            f"{synth_dir / 'orfs_logs'}:/OpenROAD-flow-scripts/flow/logs",
            f"{synth_dir / 'orfs_reports'}:/OpenROAD-flow-scripts/flow/reports",
        ]

        # ── Phase 1: Full P&R (finish picks up from synth) ──
        try:
            pnr_result = run_docker_command(
                command="make DESIGN_CONFIG=/workspace/config.mk finish",
                workspace_path=str(synth_dir),
                volumes=volumes,
                timeout=PNR_TIMEOUT,
            )
        except Exception as e:
            result["pnr_error"] = f"P&R exception: {e}"
            return result

        (synth_dir / "pnr_stdout.txt").write_text(pnr_result.get("stdout", ""))
        (synth_dir / "pnr_stderr.txt").write_text(pnr_result.get("stderr", ""))

        if not pnr_result.get("success"):
            result["pnr_error"] = f"P&R failed: {pnr_result.get('stderr', '')[-300:]}"
            return result

        result["pnr_pass"] = True

        # Check GDS
        gds_files = list((synth_dir / "orfs_results").rglob("6_final.gds"))
        result["gds_generated"] = len(gds_files) > 0

        # ── Parse finish report for timing/power ──
        self._parse_finish_report(synth_dir, result)

        # ── Phase 2: DRC ──
        if self.enable_drc:
            result.update(self._run_drc(synth_dir, volumes))

        # ── Phase 3: LVS (best-effort) ──
        if self.enable_lvs:
            result.update(self._run_lvs(synth_dir, volumes))

        return result

    @staticmethod
    def _parse_finish_report(synth_dir: Path, result: dict) -> None:
        """Parse 6_finish.rpt for WNS, TNS, and power."""
        try:
            for rpt in (synth_dir / "orfs_reports").rglob("6_finish.rpt"):
                text = rpt.read_text()
                # WNS
                m = re.search(r"wns\s+max\s+([0-9.eE+-]+)", text)
                if m and result.get("wns_ns") is None:
                    result["wns_ns"] = float(m.group(1))
                # TNS
                m = re.search(r"tns\s+max\s+([0-9.eE+-]+)", text)
                if m:
                    result["tns_ns"] = float(m.group(1))
                # Power: "Total  <int> <switch> <leak> <total> 100.0%"
                # Match the last column before "100.0%"
                for line in text.splitlines():
                    if line.strip().startswith("Total") and "100" in line:
                        parts = line.split()
                        # Total <internal> <switching> <leakage> <total> <pct>
                        if len(parts) >= 5:
                            try:
                                total_w = float(parts[-2])
                                result["power_uw"] = total_w * 1e6
                            except (ValueError, IndexError):
                                pass
                break
        except Exception:
            pass

    @staticmethod
    def _run_drc(synth_dir: Path, volumes: list[str]) -> dict:
        """Run KLayout DRC and parse violation count."""
        from tools.run_docker import run_docker_command

        result: dict = {"drc_pass": None, "drc_violations": None}
        try:
            drc_result = run_docker_command(
                command="make DESIGN_CONFIG=/workspace/config.mk drc",
                workspace_path=str(synth_dir),
                volumes=volumes,
                timeout=DRC_TIMEOUT,
            )
        except Exception as e:
            result["drc_error"] = f"DRC exception: {e}"
            return result

        if not drc_result.get("success"):
            result["drc_error"] = f"DRC failed: {drc_result.get('stderr', '')[-200:]}"
            return result

        # Parse 6_drc_count.rpt
        try:
            for f in (synth_dir / "orfs_reports").rglob("6_drc_count.rpt"):
                count_str = f.read_text().strip()
                violations = int(count_str) if count_str.isdigit() else -1
                result["drc_violations"] = violations
                result["drc_pass"] = violations == 0
                break
        except Exception:
            result["drc_pass"] = False

        return result

    @staticmethod
    def _run_lvs(synth_dir: Path, volumes: list[str]) -> dict:
        """Run KLayout LVS, fixing sky130hd CDL 'short' keyword that KLayout can't parse."""
        from tools.run_docker import run_docker_command

        result: dict = {"lvs_pass": None, "lvs_error": None}

        # sky130 CDL has two issues KLayout 0.30.x can't handle:
        # 1. "rXX ... short" resistor lines (zero-ohm connections) — delete them
        # 2. "/" separator between pins and subcircuit name — remove it
        lvs_cmd = (
            "sed -i -e '/ short$/d' -e 's| / | |g' "
            "/OpenROAD-flow-scripts/flow/platforms/sky130hd/cdl/sky130hd.cdl && "
            "make DESIGN_CONFIG=/workspace/config.mk lvs"
        )

        try:
            lvs_result = run_docker_command(
                command=lvs_cmd,
                workspace_path=str(synth_dir),
                volumes=volumes,
                timeout=LVS_TIMEOUT,
            )
        except Exception as e:
            result["lvs_error"] = f"LVS exception: {e}"
            return result

        if lvs_result.get("success"):
            result["lvs_pass"] = True
        else:
            stderr = lvs_result.get("stderr", "")
            if "Can't find a value for a R, C or L device" in stderr:
                result["lvs_error"] = "KLayout CDL parse error (platform issue)"
            else:
                result["lvs_pass"] = False
                result["lvs_error"] = stderr[-200:]

        return result
