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
RTLLM_PASS_PATTERN = re.compile(r"Your Design Passed")
BASH_TIMEOUT = 120
SIM_TIMEOUT = 30
SYNTH_TIMEOUT = 600
PNR_TIMEOUT = 900
DRC_TIMEOUT = 300
LVS_TIMEOUT = 300
STA_TIMEOUT = 120
GLS_TIMEOUT = 120
MIN_DIE_SIDE_UM = 50  # minimum die side for sky130hd PDN straps

# ── sky130 cell simulation models (via volare PDK manager) ──────────
SKY130_VOLARE_VERSION = "c6d73a35f524070e85faff4a6a9eef49553ebc2b"
SKY130_VOLARE_VERILOG_DIR = (
    Path.home() / ".volare" / "volare" / "sky130" / "versions"
    / SKY130_VOLARE_VERSION / "sky130A" / "libs.ref" / "sky130_fd_sc_hd" / "verilog"
)
SKY130_PRIMITIVES_NAME = "primitives.v"
SKY130_CELLS_NAME = "sky130_fd_sc_hd.v"

# ── PVT Corner definitions (sky130hd) ──────────────────────────────

PVT_CORNERS = [
    {
        "name": "ss_100C_1v60",
        "lib": "sky130_fd_sc_hd__ss_100C_1v60.lib",
        "label": "SS (100°C, 1.60V)",
    },
    {
        "name": "tt_025C_1v80",
        "lib": "sky130_fd_sc_hd__tt_025C_1v80.lib",
        "label": "TT (25°C, 1.80V)",
    },
    {
        "name": "ff_n40C_1v95",
        "lib": "sky130_fd_sc_hd__ff_n40C_1v95.lib",
        "label": "FF (-40°C, 1.95V)",
    },
]


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


def _parse_ref_module_ports(ref_code: str) -> list[tuple[str, str, str]] | None:
    """Parse reference Verilog module to get ports in declaration order.

    Handles both ANSI and non-ANSI port styles, including comma-separated names.
    Returns [(direction, width_str, name), ...] in module declaration order, or None.
    """
    mod_match = re.search(r"module\s+\w+\s*\(([^)]+)\)", ref_code)
    if not mod_match:
        return None
    # Extract port names from module header (last token per comma-separated entry)
    raw_names = [p.strip().split()[-1] for p in mod_match.group(1).split(",")]
    clean_names = [n for n in raw_names if re.match(r"\w+$", n)]
    if not clean_names:
        return None

    # Parse port declarations: input/output [reg|wire|logic] [width] name1 [, name2, ...]
    port_info: dict[str, tuple[str, str]] = {}
    for m in re.finditer(
        r"(input|output)\s+(?:reg\s+|wire\s+|logic\s+)?(\[\d+:\d+\])?\s*([^;]+)",
        ref_code,
    ):
        direction = m.group(1)
        width = f" {m.group(2)}" if m.group(2) else ""
        for name in m.group(3).split(","):
            name = name.strip()
            if name and re.match(r"\w+$", name):
                port_info[name] = (direction, width)

    return [(port_info.get(n, ("input", ""))[0],
             port_info.get(n, ("input", ""))[1],
             n) for n in clean_names]


def generate_top_wrapper(
    sparkle_mod_name: str,
    sparkle_ports: list[tuple[str, str, str]],
    ref_ports: list[tuple[str, str, str]],
    sv_code: str = "",
) -> str | None:
    """Generate a TopModule wrapper mapping ref ports to sparkle ports."""
    ref_inputs = [(d, t, n) for d, t, n in ref_ports if d == "input"]
    ref_outputs = [(d, t, n) for d, t, n in ref_ports if d == "output"]
    sp_inputs = [(d, t, n) for d, t, n in sparkle_ports if d == "input"]
    sp_outputs = [(d, t, n) for d, t, n in sparkle_ports if d == "output"]

    sp_user_inputs = [(d, t, n) for d, t, n in sp_inputs if n not in ("clk", "rst")]
    ref_user_inputs = [(d, t, n) for d, t, n in ref_inputs if n != "clk"]

    input_map: dict[str, str] = {}
    # First pass: match by name (_gen_{ref_name} or exact match)
    matched_sp = set()
    for _, _, rn in ref_user_inputs:
        for _, _, sn in sp_user_inputs:
            if sn not in matched_sp and (sn == rn or sn == f"_gen_{rn}"):
                input_map[rn] = sn
                matched_sp.add(sn)
                break
    # Second pass: fall back to positional for any unmatched
    unmatched_ref = [rn for _, _, rn in ref_user_inputs if rn not in input_map]
    unmatched_sp = [sn for _, _, sn in sp_user_inputs if sn not in matched_sp]
    for rn, sn in zip(unmatched_ref, unmatched_sp):
        input_map[rn] = sn

    output_map: dict[str, str] = {}
    bundled_output = False
    if len(ref_outputs) > len(sp_outputs) == 1:
        # Sparkle bundled multiple outputs into one port — check widths match
        sp_width = _port_width(sp_outputs[0][1])
        ref_total = sum(_port_width(t) for _, t, _ in ref_outputs)
        if sp_width == ref_total:
            bundled_output = True
    if not bundled_output:
        # First pass: match by name
        matched_sp_out = set()
        for _, _, rn in ref_outputs:
            for _, _, sn in sp_outputs:
                if sn not in matched_sp_out and (sn == rn or sn == f"_gen_{rn}"):
                    output_map[rn] = sn
                    matched_sp_out.add(sn)
                    break
        # Second pass: positional fallback
        unmatched_ref_out = [rn for _, _, rn in ref_outputs if rn not in output_map]
        unmatched_sp_out = [sn for _, _, sn in sp_outputs if sn not in matched_sp_out]
        for rn, sn in zip(unmatched_ref_out, unmatched_sp_out):
            output_map[rn] = sn

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
        # Bit-slice the single bundled output.
        # Sparkle packs via concat: {field_a, field_b} where field_a = MSB.
        # Try to infer correct mapping by matching field names in the concat
        # against ref output names. Fall back to positional MSB-first.
        sp_out_name = sp_outputs[0][2]
        sp_width = _port_width(sp_outputs[0][1])

        # Try to find the concat that feeds the output port (may be indirect)
        concat_order = None
        # Direct: assign out = {a, b}
        concat_pat = re.search(
            rf"assign\s+{re.escape(sp_out_name)}\s*=\s*\{{([^}}]+)\}}",
            sv_code,
        )
        if not concat_pat:
            # Indirect: assign out = _tmp; assign _tmp = {a, b}
            indirect = re.search(
                rf"assign\s+{re.escape(sp_out_name)}\s*=\s*(\w+)\s*;",
                sv_code,
            )
            if indirect:
                tmp_name = indirect.group(1)
                concat_pat = re.search(
                    rf"assign\s+{re.escape(tmp_name)}\s*=\s*\{{([^}}]+)\}}",
                    sv_code,
                )
        if concat_pat:
            fields = [f.strip() for f in concat_pat.group(1).split(",")]
            # Map _gen_X fields to ref names: _gen_sum -> sum
            concat_order = []
            for f in fields:
                base = f.replace("_gen_", "") if f.startswith("_gen_") else f
                concat_order.append(base)

        # Check if concat field names actually match ref output names
        use_concat = False
        if concat_order and len(concat_order) == len(ref_outputs):
            ref_names = {rn for _, _, rn in ref_outputs}
            if all(f in ref_names for f in concat_order):
                use_concat = True

        if use_concat:
            # Use concat field order: first field = MSB
            offset = sp_width
            for field_name in concat_order:
                # Find matching ref output
                matched_ref = None
                for _, rt, rn in ref_outputs:
                    if rn == field_name:
                        matched_ref = (rt, rn)
                        break
                if matched_ref:
                    rt, rn = matched_ref
                    w = _port_width(rt)
                    high = offset - 1
                    low = offset - w
                    if w == 1:
                        lines.append(f"    assign {rn} = {sp_out_name}_wire[{low}];")
                    else:
                        lines.append(f"    assign {rn} = {sp_out_name}_wire[{high}:{low}];")
                    offset -= w
        else:
            # Fallback: positional, first ref output = MSB
            offset = sp_width
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

    def __init__(self, project_root: Path = Path("."), enable_synth: bool = False, enable_pnr: bool = False, enable_drc: bool = False, enable_lvs: bool = False, enable_corners: bool = False, enable_gls: bool = False, lean_repl=None, dataset: str = "verilogeval", dataset_obj=None):
        self.project_root = project_root.resolve()
        self.dataset_name = dataset.lower()
        self.dataset_obj = dataset_obj  # Optional Dataset instance from dataset.py
        self.dataset_dir = self.project_root / "verilog-eval" / "dataset_spec-to-rtl"
        self.enable_pnr = enable_pnr or enable_drc or enable_lvs  # drc/lvs imply pnr
        self.enable_synth = enable_synth or self.enable_pnr  # pnr implies synth
        self.enable_gls = enable_gls and self.enable_synth  # gls requires synth
        self.enable_drc = enable_drc
        self.enable_lvs = enable_lvs
        self.enable_corners = enable_corners and self.enable_pnr  # corners require pnr
        self.lean_repl = lean_repl  # Optional LeanREPL instance for fast compilation

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
            "gls_synth_status": "not_run",   # post-synth gate-level sim
            "gls_synth_mismatches": -1,
            "gls_pnr_status": "not_run",     # post-PnR gate-level sim
            "gls_pnr_mismatches": -1,
            "has_sorry": True,       # True = unverified (default), False = formally verified
            "detail": "",
        }

        # 1. Compile
        lean_file = self.project_root / "Generated" / f"{prob_id}.lean"
        if not lean_file.exists():
            result["detail"] = f"Lean file not found: Generated/{prob_id}.lean"
            return result

        sv_code = None

        if self.lean_repl is not None:
            # ── Fast path: use persistent REPL (~0.1s) ──
            repl_result = self.lean_repl.check_file(lean_file)
            if not repl_result.passed:
                result["detail"] = f"Compile failed:\n{repl_result.error_text[:1000]}"
                return result
            result["compile_pass"] = True
            result["has_sorry"] = not repl_result.complete
            sv_code = repl_result.verilog
            # Strip trailing non-Verilog content (e.g., Lean comments after endmodule)
            if sv_code:
                last_end = sv_code.rfind('endmodule')
                if last_end >= 0:
                    sv_code = sv_code[:last_end + len('endmodule')]
        else:
            # ── Fallback: lake build (~10s) ──
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
            result["has_sorry"] = bool(re.search(r"declaration uses `sorry`", build_output))
            sv_code = self._extract_sv(build_output)

        # 2. Extract SystemVerilog
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

            # 5b. Post-synthesis gate-level simulation
            if self.enable_gls and result["synth_pass"]:
                synth_dir = run_dir / "synth" / prob_id
                gls_result = self._run_gls(
                    prob_id, sv_code, sparkle_mod_name, sparkle_ports,
                    run_dir, synth_dir, "post_synth",
                )
                result.update(gls_result)

            # 6. Full P&R + DRC + LVS (optional)
            if self.enable_pnr and result["synth_pass"]:
                pnr_result = self._run_pnr(
                    prob_id, sv_code, sparkle_mod_name or prob_id.lower(), run_dir
                )
                result.update(pnr_result)

                # 6b. Post-PnR gate-level simulation
                if self.enable_gls and result["pnr_pass"]:
                    synth_dir = run_dir / "synth" / prob_id
                    gls_result = self._run_gls(
                        prob_id, sv_code, sparkle_mod_name, sparkle_ports,
                        run_dir, synth_dir, "post_pnr",
                    )
                    result.update(gls_result)

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
        """Run simulation — dispatches to VerilogEval or RTLLM mode."""
        if self.dataset_name == "rtllm":
            return self._run_sim_rtllm(prob_id, sv_code, sparkle_mod_name, sparkle_ports, run_dir)
        return self._run_sim_verilogeval(prob_id, sv_code, sparkle_mod_name, sparkle_ports, run_dir)

    def _run_sim_verilogeval(
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

        wrapper = generate_top_wrapper(sparkle_mod_name, sparkle_ports, ref_ports, sv_code)
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

    def _run_sim_rtllm(
        self, prob_id: str, sv_code: str,
        sparkle_mod_name: str, sparkle_ports: list[tuple[str, str, str]],
        run_dir: Path,
    ) -> tuple[str, int, str]:
        """Run iverilog simulation against RTLLM testbench.

        RTLLM testbenches instantiate the design by its name (e.g. adder_8bit uut(...)).
        We generate a wrapper module with the design name that instantiates the Sparkle module.
        Pass is detected by 'Your Design Passed' in output.
        """
        if self.dataset_obj is None:
            return "sim_error", -1, "RTLLM dataset object not set on evaluator"

        info = self.dataset_obj.load_problem(prob_id)
        tb_path = info.testbench_path
        design_name = info.design_name

        if not tb_path.exists():
            return "sim_error", -1, f"Testbench not found: {tb_path}"

        if not sparkle_mod_name:
            return "sim_error", -1, "Could not parse Sparkle module name"

        sim_dir = run_dir / "sim" / prob_id
        sim_dir.mkdir(parents=True, exist_ok=True)

        # Rename Sparkle module if it collides with design_name
        # (wrapper must use design_name, so the inner module needs a distinct name)
        if sparkle_mod_name == design_name:
            renamed = f"{sparkle_mod_name}_sparkle"
            sv_code = re.sub(
                rf'\bmodule\s+{re.escape(sparkle_mod_name)}\b',
                f'module {renamed}',
                sv_code,
                count=1,
            )
            sparkle_mod_name = renamed

        (sim_dir / "sparkle_dut.sv").write_text(sv_code)

        # Generate wrapper: module <design_name>(...); sparkle_mod dut(...); endmodule
        # Parse testbench to extract expected ports from the DUT instantiation
        tb_code = tb_path.read_text()
        wrapper = self._generate_rtllm_wrapper(
            design_name, sparkle_mod_name, sparkle_ports, tb_code, sv_code,
            info.ref_code,
        )
        if not wrapper:
            return "sim_error", -1, "Could not generate RTLLM wrapper"

        compile_files = [str(sim_dir / "sparkle_dut.sv")]
        if wrapper:
            (sim_dir / "wrapper.sv").write_text(wrapper)
            compile_files.append(str(sim_dir / "wrapper.sv"))
        compile_files.append(str(tb_path))

        # Compile
        try:
            comp = subprocess.run(
                ["iverilog", "-g2012", "-o", str(sim_dir / "sim.vvp")] + compile_files,
                capture_output=True, text=True, timeout=30,
            )
            if comp.returncode != 0:
                (sim_dir / "compile_error.txt").write_text(comp.stderr)
                return "sim_error", -1, f"iverilog compile failed:\n{comp.stderr[:500]}"
        except subprocess.TimeoutExpired:
            return "sim_error", -1, "iverilog compile timeout"
        except FileNotFoundError:
            return "sim_error", -1, "iverilog not found"

        # Copy data files needed by $readmemh in testbenches (e.g. wfull.txt)
        tb_dir = tb_path.parent
        for data_file in tb_dir.iterdir():
            if data_file.is_file() and data_file.suffix in (".txt", ".dat", ".hex", ".mem"):
                dest = sim_dir / data_file.name
                if not dest.exists():
                    shutil.copy2(data_file, dest)

        # Run simulation (cwd=sim_dir so $readmemh finds copied data files)
        try:
            sim = subprocess.run(
                ["vvp", str(sim_dir / "sim.vvp")],
                capture_output=True, text=True, timeout=SIM_TIMEOUT,
                cwd=str(sim_dir),
            )
            sim_output = sim.stdout + sim.stderr
            (sim_dir / "sim_output.txt").write_text(sim_output)
        except subprocess.TimeoutExpired:
            return "sim_error", -1, "Simulation timeout"

        # Parse results — RTLLM uses "Your Design Passed"
        if RTLLM_PASS_PATTERN.search(sim_output):
            return "sim_pass", 0, "Your Design Passed"

        # Check for failure pattern
        fail_m = re.search(r"(\d+)\s*/\s*\d+\s*failures", sim_output)
        if fail_m:
            failures = int(fail_m.group(1))
            return "sim_fail", failures, f"{failures} failures"

        if "TIMEOUT" in sim_output:
            return "sim_error", -1, "Simulation hit internal timeout"

        return "sim_fail", -1, f"Design did not pass:\n{sim_output[:300]}"

    # ── Gate-Level Simulation (GLS) ──────────────────────────────────

    @staticmethod
    def _ensure_sky130_cache() -> Path:
        """Ensure sky130 cell simulation models are available via volare.

        Returns the directory containing primitives.v and sky130_fd_sc_hd.v.
        """
        verilog_dir = SKY130_VOLARE_VERILOG_DIR
        prim = verilog_dir / SKY130_PRIMITIVES_NAME
        cells = verilog_dir / SKY130_CELLS_NAME
        if prim.exists() and cells.exists():
            return verilog_dir
        # Try to fetch via volare
        try:
            subprocess.run(
                ["volare", "fetch", "--pdk", "sky130", SKY130_VOLARE_VERSION],
                capture_output=True, text=True, timeout=300,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        if not prim.exists() or not cells.exists():
            raise RuntimeError(
                f"sky130 cell models not found at {verilog_dir}. "
                f"Install with: pip install volare && volare fetch --pdk sky130 {SKY130_VOLARE_VERSION}"
            )
        return verilog_dir

    def _run_gls(
        self, prob_id: str, sv_code: str,
        sparkle_mod_name: str, sparkle_ports: list[tuple[str, str, str]],
        run_dir: Path, synth_dir: Path, stage: str,
    ) -> dict:
        """Run gate-level simulation after synthesis or P&R.

        Args:
            stage: "post_synth" or "post_pnr"
        Returns dict with gls_{stage}_status and gls_{stage}_mismatches.
        """
        # Locate netlist on local filesystem
        # ORFS naming: post-synth = 1_*_yosys.v, post-PnR = 6_1_merged.v or 6_final.v
        if stage == "post_synth":
            netlist_glob = "1_*_yosys.v"
        else:
            netlist_glob = "6_1_merged.v"
        netlists = list((synth_dir / "orfs_results").rglob(netlist_glob))
        if not netlists and stage == "post_pnr":
            # Fallback: try 6_final.v
            netlists = list((synth_dir / "orfs_results").rglob("6_final.v"))
        if not netlists:
            key = "synth" if stage == "post_synth" else "pnr"
            return {
                f"gls_{key}_status": "sim_error",
                f"gls_{key}_mismatches": -1,
            }
        netlist_path = netlists[0]

        if self.dataset_name == "rtllm":
            status, mismatches, detail = self._run_gls_rtllm(
                prob_id, sv_code, sparkle_mod_name, sparkle_ports,
                run_dir, synth_dir, netlist_path, stage,
            )
        else:
            status, mismatches, detail = self._run_gls_verilogeval(
                prob_id, sv_code, sparkle_mod_name, sparkle_ports,
                run_dir, synth_dir, netlist_path, stage,
            )

        key = "synth" if stage == "post_synth" else "pnr"
        return {
            f"gls_{key}_status": status,
            f"gls_{key}_mismatches": mismatches,
        }

    def _run_gls_verilogeval(
        self, prob_id: str, sv_code: str,
        sparkle_mod_name: str, sparkle_ports: list[tuple[str, str, str]],
        run_dir: Path, synth_dir: Path,
        netlist_path: Path, stage: str,
    ) -> tuple[str, int, str]:
        """Run gate-level simulation for VerilogEval using local iverilog."""
        sky130_dir = self._ensure_sky130_cache()

        ref_sv_path = self.dataset_dir / f"{prob_id}_ref.sv"
        test_sv_path = self.dataset_dir / f"{prob_id}_test.sv"
        if not ref_sv_path.exists() or not test_sv_path.exists():
            return "sim_error", -1, f"Missing ref or test SV for {prob_id}"

        # Regenerate wrapper (same as RTL sim)
        ref_sv = ref_sv_path.read_text()
        ref_ports = parse_ref_ports(ref_sv)
        if not sparkle_mod_name:
            return "sim_error", -1, "Could not parse Sparkle module name"
        wrapper = generate_top_wrapper(sparkle_mod_name, sparkle_ports, ref_ports, sv_code)
        if not wrapper:
            return "sim_error", -1, "Could not generate TopModule wrapper"

        # Stage files
        gls_dir = synth_dir / f"gls_{stage}"
        gls_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ref_sv_path, gls_dir / "ref.sv")
        shutil.copy2(test_sv_path, gls_dir / "test.sv")
        (gls_dir / "wrapper.sv").write_text(wrapper)

        # Compile with local iverilog
        vvp_file = gls_dir / "gls.vvp"
        compile_cmd = [
            "iverilog", "-g2012", "-DFUNCTIONAL", "-DUNIT_DELAY=#0",
            "-o", str(vvp_file),
            str(sky130_dir / SKY130_PRIMITIVES_NAME),
            str(sky130_dir / SKY130_CELLS_NAME),
            str(netlist_path),
            str(gls_dir / "ref.sv"),
            str(gls_dir / "wrapper.sv"),
            str(gls_dir / "test.sv"),
        ]

        try:
            comp = subprocess.run(
                compile_cmd, capture_output=True, text=True, timeout=GLS_TIMEOUT,
            )
            if comp.returncode != 0:
                (gls_dir / "gls_compile_error.txt").write_text(comp.stderr)
                return "sim_error", -1, f"GLS compile failed:\n{comp.stderr[:500]}"

            sim = subprocess.run(
                ["vvp", str(vvp_file)],
                capture_output=True, text=True, timeout=GLS_TIMEOUT,
                cwd=str(gls_dir),
            )
        except subprocess.TimeoutExpired:
            return "sim_error", -1, f"GLS {stage}: simulation timeout"
        except Exception as e:
            return "sim_error", -1, f"GLS error: {e}"

        sim_output = sim.stdout + sim.stderr
        (gls_dir / "gls_output.txt").write_text(sim_output)

        # Parse VerilogEval output
        m = re.search(r"Mismatches:\s*(\d+)\s+in\s+(\d+)\s+samples", sim_output)
        if m:
            mismatches = int(m.group(1))
            total = int(m.group(2))
            if mismatches == 0:
                return "sim_pass", 0, f"GLS {stage}: 0 mismatches in {total} samples"
            return "sim_fail", mismatches, f"GLS {stage}: {mismatches} mismatches in {total} samples"

        if "TIMEOUT" in sim_output:
            return "sim_error", -1, f"GLS {stage}: simulation timeout"

        return "sim_error", -1, f"GLS {stage}: could not parse output:\n{sim_output[:300]}"

    def _run_gls_rtllm(
        self, prob_id: str, sv_code: str,
        sparkle_mod_name: str, sparkle_ports: list[tuple[str, str, str]],
        run_dir: Path, synth_dir: Path,
        netlist_path: Path, stage: str,
    ) -> tuple[str, int, str]:
        """Run gate-level simulation for RTLLM using local iverilog."""
        sky130_dir = self._ensure_sky130_cache()

        if self.dataset_obj is None:
            return "sim_error", -1, "RTLLM dataset object not set on evaluator"

        info = self.dataset_obj.load_problem(prob_id)
        tb_path = info.testbench_path
        design_name = info.design_name

        if not tb_path.exists():
            return "sim_error", -1, f"Testbench not found: {tb_path}"

        # Stage files
        gls_dir = synth_dir / f"gls_{stage}"
        gls_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tb_path, gls_dir / "testbench.v")

        # Copy data files for $readmemh
        tb_dir = tb_path.parent
        for data_file in tb_dir.iterdir():
            if data_file.is_file() and data_file.suffix in (".txt", ".dat", ".hex", ".mem"):
                dest = gls_dir / data_file.name
                if not dest.exists():
                    shutil.copy2(data_file, dest)

        # Build compile file list
        compile_files = [str(netlist_path)]

        if sparkle_mod_name != design_name:
            tb_code = tb_path.read_text()
            wrapper = self._generate_rtllm_wrapper(
                design_name, sparkle_mod_name, sparkle_ports,
                tb_code, sv_code, info.ref_code,
            )
            if not wrapper:
                return "sim_error", -1, "Could not generate RTLLM GLS wrapper"
            (gls_dir / "wrapper.sv").write_text(wrapper)
            compile_files.append(str(gls_dir / "wrapper.sv"))

        compile_files.append(str(gls_dir / "testbench.v"))

        # Compile with local iverilog
        vvp_file = gls_dir / "gls.vvp"
        compile_cmd = [
            "iverilog", "-g2012", "-DFUNCTIONAL", "-DUNIT_DELAY=#0",
            "-o", str(vvp_file),
            str(sky130_dir / SKY130_PRIMITIVES_NAME),
            str(sky130_dir / SKY130_CELLS_NAME),
        ] + compile_files

        try:
            comp = subprocess.run(
                compile_cmd, capture_output=True, text=True, timeout=GLS_TIMEOUT,
            )
            if comp.returncode != 0:
                (gls_dir / "gls_compile_error.txt").write_text(comp.stderr)
                return "sim_error", -1, f"GLS compile failed:\n{comp.stderr[:500]}"

            sim = subprocess.run(
                ["vvp", str(vvp_file)],
                capture_output=True, text=True, timeout=GLS_TIMEOUT,
                cwd=str(gls_dir),
            )
        except subprocess.TimeoutExpired:
            return "sim_error", -1, f"GLS {stage}: simulation timeout"
        except Exception as e:
            return "sim_error", -1, f"GLS error: {e}"

        sim_output = sim.stdout + sim.stderr
        (gls_dir / "gls_output.txt").write_text(sim_output)

        # Parse RTLLM output
        if RTLLM_PASS_PATTERN.search(sim_output):
            return "sim_pass", 0, f"GLS {stage}: Your Design Passed"

        fail_m = re.search(r"(\d+)\s*/\s*\d+\s*failures", sim_output)
        if fail_m:
            failures = int(fail_m.group(1))
            return "sim_fail", failures, f"GLS {stage}: {failures} failures"

        if "TIMEOUT" in sim_output:
            return "sim_error", -1, f"GLS {stage}: simulation timeout"

        return "sim_fail", -1, f"GLS {stage}: Design did not pass:\n{sim_output[:300]}"

    @staticmethod
    def _generate_rtllm_wrapper(
        design_name: str,
        sparkle_mod_name: str,
        sparkle_ports: list[tuple[str, str, str]],
        tb_code: str,
        sv_code: str = "",
        ref_code: str = "",
    ) -> str | None:
        """Generate a wrapper module named <design_name> that instantiates the Sparkle module.

        Parses the testbench to find the DUT instantiation and extract expected ports.
        Handles bundled outputs (Sparkle packs multiple outputs into one port).
        """
        # Parse testbench DUT instantiation to get expected port connections
        # Handles optional #(params) with one level of nested parens
        inst_pattern = re.compile(
            rf"{re.escape(design_name)}\s+(?:#\s*\((?:[^()]*|\([^()]*\))*\)\s*)?\w+\s*\(([^;]+)\)\s*;",
            re.DOTALL,
        )
        inst_match = inst_pattern.search(tb_code)
        if not inst_match:
            return None

        inst_body = inst_match.group(1)
        tb_ports = re.findall(r"\.(\w+)\s*\(", inst_body)

        # Fallback for positional connections: use reference design port names
        ref_port_lookup: dict[str, tuple[str, str]] = {}
        if not tb_ports and ref_code:
            ref_ordered = _parse_ref_module_ports(ref_code)
            if ref_ordered:
                tb_ports = [name for _, _, name in ref_ordered]
                ref_port_lookup = {name: (d, w) for d, w, name in ref_ordered}

        if not tb_ports:
            return None

        sp_port_map = {n: (d, t) for d, t, n in sparkle_ports}
        sp_port_map_lower = {n.lower(): n for n in sp_port_map}  # for case-insensitive fallback
        sp_outputs = [(d, t, n) for d, t, n in sparkle_ports if d == "output"]

        # Classify each testbench port: match to Sparkle port or infer from TB
        tb_classified = []  # (pname, direction, width_str, sparkle_name_or_None)
        for pname in tb_ports:
            sp_match = None
            if pname in sp_port_map:
                sp_match = pname
            else:
                gen_name = f"_gen_{pname}"
                if gen_name in sp_port_map:
                    sp_match = gen_name
                else:
                    # Case-insensitive fallback
                    pl = pname.lower()
                    gen_lower = f"_gen_{pl}"
                    if pl in sp_port_map_lower:
                        sp_match = sp_port_map_lower[pl]
                    elif gen_lower in sp_port_map_lower:
                        sp_match = sp_port_map_lower[gen_lower]

            if sp_match:
                d, t = sp_port_map[sp_match]
                wm = re.search(r"\[(\d+):(\d+)\]", t)
                width = f" [{wm.group(1)}:{wm.group(2)}]" if wm else ""
                tb_classified.append((pname, d, width, sp_match))
            else:
                if ref_port_lookup and pname in ref_port_lookup:
                    direction, width = ref_port_lookup[pname]
                else:
                    tb_decl = re.search(
                        rf"(reg|wire)\s*(\[\d+:\d+\])?\s*{re.escape(pname)}\s*;",
                        tb_code,
                    )
                    if tb_decl:
                        kind = tb_decl.group(1)
                        width = f" {tb_decl.group(2)}" if tb_decl.group(2) else ""
                        direction = "input" if kind == "reg" else "output"
                    else:
                        direction, width = "input", ""
                tb_classified.append((pname, direction, width, None))

        # Detect bundled output: unmatched TB outputs whose total width == single Sparkle output
        unmatched_outs = [(p, w) for p, d, w, m in tb_classified if d == "output" and m is None]
        bundled_sp = None
        if unmatched_outs and len(sp_outputs) == 1:
            sp_out_d, sp_out_t, sp_out_n = sp_outputs[0]
            sp_w = _port_width(sp_out_t)
            tb_total = 0
            for _, w in unmatched_outs:
                wm = re.search(r"\[(\d+):(\d+)\]", w)
                tb_total += (abs(int(wm.group(1)) - int(wm.group(2))) + 1) if wm else 1
            if sp_w == tb_total:
                bundled_sp = (sp_out_n, sp_out_t)

        # ── Build wrapper ──
        lines = [f"module {design_name} ("]
        port_decls = [f"    {d}{w} {p}" for p, d, w, _ in tb_classified]
        lines.append(",\n".join(port_decls))
        lines.append(");")
        lines.append("")

        # Wire for bundled output
        if bundled_sp:
            sp_out_n, sp_out_t = bundled_sp
            wm = re.search(r"\[(\d+):(\d+)\]", sp_out_t)
            wdecl = f" [{wm.group(1)}:{wm.group(2)}]" if wm else ""
            lines.append(f"    wire{wdecl} {sp_out_n}_wire;")

        # Wires for directly-matched outputs
        for p, d, w, m in tb_classified:
            if d == "output" and m is not None:
                lines.append(f"    wire{w} {m}_wire;")

        lines.append("")

        # Instantiate Sparkle module
        lines.append(f"    {sparkle_mod_name} sparkle_dut (")
        inst_conns = []
        for _, _, sn in sparkle_ports:
            # Check if this is a matched input
            matched_input = next(
                (p for p, d, _, m in tb_classified if d == "input" and m == sn), None
            )
            if matched_input:
                inst_conns.append(f"        .{sn}({matched_input})")
                continue
            # Check if this is a matched output
            matched_output = next(
                (p for p, d, _, m in tb_classified if d == "output" and m == sn), None
            )
            if matched_output:
                inst_conns.append(f"        .{sn}({sn}_wire)")
                continue
            # Check if this is the bundled output
            if bundled_sp and sn == bundled_sp[0]:
                inst_conns.append(f"        .{sn}({sn}_wire)")
                continue
            # Unmatched: handle clk/rst, _gen_ prefix, or tie off
            if sn == "clk":
                clk_sig = "clk" if "clk" in tb_ports else "1'b0"
                inst_conns.append(f"        .clk({clk_sig})")
            elif sn == "rst":
                rst_sig = "rst" if "rst" in tb_ports else ("reset" if "reset" in tb_ports else "1'b0")
                inst_conns.append(f"        .rst({rst_sig})")
            elif sn.startswith("_gen_"):
                base = sn[5:]
                matched_tb = next((tp for tp in tb_ports if tp.lower() == base.lower()), None)
                inst_conns.append(f"        .{sn}({matched_tb if matched_tb else sn})")
            else:
                inst_conns.append(f"        .{sn}({sn})")
        lines.append(",\n".join(inst_conns))
        lines.append("    );")

        # ── Output assignments ──
        if bundled_sp:
            sp_out_n, sp_out_t = bundled_sp
            sp_w = _port_width(sp_out_t)

            # Try to determine concat field order from SV code
            concat_order = None
            concat_pat = re.search(
                rf"assign\s+{re.escape(sp_out_n)}\s*=\s*\{{([^}}]+)\}}", sv_code
            )
            if not concat_pat:
                indirect = re.search(
                    rf"assign\s+{re.escape(sp_out_n)}\s*=\s*(\w+)\s*;", sv_code
                )
                if indirect:
                    concat_pat = re.search(
                        rf"assign\s+{re.escape(indirect.group(1))}\s*=\s*\{{([^}}]+)\}}", sv_code
                    )
            if concat_pat:
                fields = [f.strip().replace("_gen_", "") for f in concat_pat.group(1).split(",")]
                tb_out_names = {n for n, _ in unmatched_outs}
                if len(fields) == len(unmatched_outs) and all(f in tb_out_names for f in fields):
                    concat_order = fields

            ordered = concat_order or [n for n, _ in unmatched_outs]
            offset = sp_w
            for field_name in ordered:
                for n, w in unmatched_outs:
                    if n == field_name:
                        wm = re.search(r"\[(\d+):(\d+)\]", w)
                        fw = (abs(int(wm.group(1)) - int(wm.group(2))) + 1) if wm else 1
                        high, low = offset - 1, offset - fw
                        if fw == 1:
                            lines.append(f"    assign {n} = {sp_out_n}_wire[{low}];")
                        else:
                            lines.append(f"    assign {n} = {sp_out_n}_wire[{high}:{low}];")
                        offset -= fw
                        break

        # Direct-mapped outputs
        for p, d, _, m in tb_classified:
            if d == "output" and m is not None:
                lines.append(f"    assign {p} = {m}_wire;")

        lines.append("endmodule")
        return "\n".join(lines)

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

        # Set up output directories (clean first to avoid stale results from prior iterations)
        results_dir = synth_dir / "orfs_results"
        logs_dir = synth_dir / "orfs_logs"
        reports_dir = synth_dir / "orfs_reports"
        for d in [results_dir, logs_dir, reports_dir]:
            if d.exists():
                shutil.rmtree(d)
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

        # ── Phase 2: Multi-corner STA ──
        if self.enable_corners:
            corner_result = self._run_multi_corner_sta(
                prob_id, sv_code, top_module, synth_dir, volumes
            )
            result.update(corner_result)

        # ── Phase 3: DRC ──
        if self.enable_drc:
            result.update(self._run_drc(synth_dir, volumes))

        # ── Phase 4: LVS (best-effort) ──
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

    def _run_multi_corner_sta(
        self, prob_id: str, sv_code: str, top_module: str,
        synth_dir: Path, volumes: list[str],
    ) -> dict:
        """Run STA at each PVT corner on the post-route netlist."""
        from tools.run_docker import run_docker_command

        has_clk = bool(re.search(r'\binput\b.*\bclk\b', sv_code))
        corners_data: list[dict] = []
        platform_dir = "/OpenROAD-flow-scripts/flow/platforms/sky130hd"
        results_base = f"/OpenROAD-flow-scripts/flow/results/sky130hd/{top_module}/base"

        for corner in PVT_CORNERS:
            cname = corner["name"]
            lib_path = f"{platform_dir}/lib/{corner['lib']}"

            # TCL script for this corner
            tcl_lines = [
                f"read_liberty {lib_path}",
                f"read_verilog {results_base}/6_final.v",
                f"link_design {top_module}",
                "read_sdc /workspace/constraints.sdc",
                f"catch {{ read_spef {results_base}/6_final.spef }}",
            ]
            if has_clk:
                tcl_lines += [
                    "report_checks -path_delay max -digits 4",
                    "report_checks -path_delay min -digits 4",
                ]
            tcl_lines += [
                "report_power",
                "exit",
            ]

            tcl_file = synth_dir / f"sta_{cname}.tcl"
            tcl_file.write_text("\n".join(tcl_lines) + "\n")

            try:
                sta_result = run_docker_command(
                    command=f"/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/sta /workspace/sta_{cname}.tcl",
                    workspace_path=str(synth_dir),
                    volumes=volumes,
                    timeout=STA_TIMEOUT,
                )
            except Exception as e:
                corners_data.append({
                    "corner": cname, "label": corner["label"],
                    "error": str(e),
                })
                continue

            stdout = sta_result.get("stdout", "")
            (synth_dir / f"sta_{cname}_out.txt").write_text(stdout)

            cdata: dict = {
                "corner": cname,
                "label": corner["label"],
                "wns_ns": None,
                "whs_ns": None,
                "power_uw": None,
            }

            # Parse setup WNS and hold WHS from slack lines
            setup_slacks = re.findall(
                r"slack\s+\((?:MET|VIOLATED)\)\s+([0-9.eE+-]+)", stdout
            )
            if setup_slacks:
                cdata["wns_ns"] = float(setup_slacks[0])
            if len(setup_slacks) >= 2:
                cdata["whs_ns"] = float(setup_slacks[1])

            # Parse power: "Total  <int> <switch> <leak> <total>"
            for line in stdout.splitlines():
                if line.strip().startswith("Total"):
                    parts = line.split()
                    if len(parts) >= 5:
                        try:
                            cdata["power_uw"] = float(parts[-2]) * 1e6
                        except (ValueError, IndexError):
                            pass

            corners_data.append(cdata)

        # Compute worst-case across corners
        worst: dict = {
            "pvt_corners": corners_data,
            "pvt_worst_wns_ns": None,
            "pvt_worst_whs_ns": None,
            "pvt_worst_power_uw": None,
        }
        wns_vals = [c["wns_ns"] for c in corners_data if c.get("wns_ns") is not None]
        whs_vals = [c["whs_ns"] for c in corners_data if c.get("whs_ns") is not None]
        pwr_vals = [c["power_uw"] for c in corners_data if c.get("power_uw") is not None]
        if wns_vals:
            worst["pvt_worst_wns_ns"] = min(wns_vals)
        if whs_vals:
            worst["pvt_worst_whs_ns"] = min(whs_vals)
        if pwr_vals:
            worst["pvt_worst_power_uw"] = max(pwr_vals)

        return worst

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
