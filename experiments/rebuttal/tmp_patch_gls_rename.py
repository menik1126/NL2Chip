#!/usr/bin/env python3
"""Patch backend evaluator GLS to rename netlist tops; enable CVDP GLS; retry launcher."""
from pathlib import Path

EVAL = Path("/home/sgli/work/NL2Chip_sparkle_416ec86_backend_eval_20260919/agent/evaluator.py")
LAUNCH = Path("/home/sgli/work/nl2chip_sparkle_416ec86_backend_eval_private_20260919/launch_backend_pd.py")

text = EVAL.read_text()
bak = EVAL.with_suffix(".py.bak_gls_rename_20260920")
if not bak.exists():
    bak.write_text(text)

old_run_gls = '''        if self.dataset_name in {"cvdp", "realbench"}:
            key = "synth" if stage == "post_synth" else "pnr"
            return {
                f"gls_{key}_status": "not_run",
                f"gls_{key}_mismatches": -1,
            }

        # Locate netlist on local filesystem
'''
new_run_gls = '''        # Locate netlist on local filesystem
'''
if old_run_gls not in text:
    raise SystemExit("could not find cvdp GLS skip block")
text = text.replace(old_run_gls, new_run_gls, 1)

old_dispatch = '''        if self.dataset_name == "resbench":
            status, mismatches, detail = self._run_gls_resbench(
                prob_id, sv_code, sparkle_mod_name, sparkle_ports,
                run_dir, synth_dir, netlist_path, stage,
            )
        elif self.dataset_name == "rtllm":
            status, mismatches, detail = self._run_gls_rtllm(
                prob_id, sv_code, sparkle_mod_name, sparkle_ports,
                run_dir, synth_dir, netlist_path, stage,
            )
        else:
            status, mismatches, detail = self._run_gls_verilogeval(
                prob_id, sv_code, sparkle_mod_name, sparkle_ports,
                run_dir, synth_dir, netlist_path, stage,
            )
'''
new_dispatch = '''        if self.dataset_name == "resbench":
            status, mismatches, detail = self._run_gls_resbench(
                prob_id, sv_code, sparkle_mod_name, sparkle_ports,
                run_dir, synth_dir, netlist_path, stage,
            )
        elif self.dataset_name == "rtllm":
            status, mismatches, detail = self._run_gls_rtllm(
                prob_id, sv_code, sparkle_mod_name, sparkle_ports,
                run_dir, synth_dir, netlist_path, stage,
            )
        elif self.dataset_name in {"cvdp", "realbench"}:
            status, mismatches, detail = self._run_gls_cvdp(
                prob_id, sv_code, sparkle_mod_name, sparkle_ports,
                run_dir, synth_dir, netlist_path, stage,
            )
        else:
            status, mismatches, detail = self._run_gls_verilogeval(
                prob_id, sv_code, sparkle_mod_name, sparkle_ports,
                run_dir, synth_dir, netlist_path, stage,
            )
'''
if old_dispatch not in text:
    raise SystemExit("could not find GLS dispatch")
text = text.replace(old_dispatch, new_dispatch, 1)

helper = '''
    @staticmethod
    def _stage_gls_netlist(
        netlist_path: Path,
        gls_dir: Path,
        candidate_names: list[str],
        inner_name: str,
    ) -> Path | None:
        """Copy ORFS netlist and rename its top so it does not collide with the TB wrapper."""
        gls_dir.mkdir(parents=True, exist_ok=True)
        text = netlist_path.read_text(errors="replace")
        renamed = False
        for top in candidate_names:
            if not top or top == inner_name:
                continue
            new_text, n = re.subn(
                rf"\\bmodule\\s+{re.escape(top)}\\b",
                f"module {inner_name}",
                text,
                count=1,
            )
            if n:
                text = new_text
                renamed = True
                break
        if not renamed:
            # Already the inner name, or unexpected top; still stage a copy.
            if re.search(rf"\\bmodule\\s+{re.escape(inner_name)}\\b", text):
                renamed = True
        if not renamed:
            return None
        staged = gls_dir / "netlist.v"
        staged.write_text(text)
        return staged

'''

# Insert helper before def _run_gls_verilogeval
marker = "    def _run_gls_verilogeval("
if helper.strip() in text:
    print("helper already present")
else:
    if marker not in text:
        raise SystemExit("no _run_gls_verilogeval marker")
    text = text.replace(marker, helper + marker, 1)

old_ve = '''        if not wrapper:
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
'''
new_ve = '''        inner_name = (
            "TopModule_sparkle"
            if (sparkle_mod_name or "TopModule") == "TopModule"
            else f"{sparkle_mod_name}_gls"
        )
        wrapper = generate_top_wrapper(
            inner_name,
            sparkle_ports,
            ref_ports,
            sv_code,
            ref_code=ref_sv,
            prompt_text=prompt_text,
        )
        if not wrapper:
            return "sim_error", -1, "Could not generate TopModule wrapper"

        # Stage files
        gls_dir = synth_dir / f"gls_{stage}"
        gls_dir.mkdir(parents=True, exist_ok=True)
        staged = self._stage_gls_netlist(
            netlist_path,
            gls_dir,
            [sparkle_mod_name, "TopModule", inner_name],
            inner_name,
        )
        if staged is None:
            return "sim_error", -1, f"Could not rename GLS netlist to {inner_name}"
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
            str(staged),
            str(gls_dir / "ref.sv"),
            str(gls_dir / "wrapper.sv"),
            str(gls_dir / "test.sv"),
        ]
'''

# The original has generate_top_wrapper before "if not wrapper" - need to replace including first wrapper gen
old_ve_full = '''        wrapper = generate_top_wrapper(
            sparkle_mod_name,
            sparkle_ports,
            ref_ports,
            sv_code,
            ref_code=ref_sv,
            prompt_text=prompt_text,
        )
''' + old_ve
if old_ve_full not in text:
    raise SystemExit("could not find VE GLS wrapper+compile block")
text = text.replace(old_ve_full, new_ve, 1)

old_rtllm = '''        # Build compile file list
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
'''
new_rtllm = '''        inner_name = sparkle_mod_name or design_name
        if sparkle_mod_name == design_name:
            inner_name = f"{design_name}_sparkle_gls"
        staged = self._stage_gls_netlist(
            netlist_path,
            gls_dir,
            [sparkle_mod_name, design_name, inner_name, "TopModule"],
            inner_name,
        )
        if staged is None:
            return "sim_error", -1, f"Could not rename RTLLM GLS netlist to {inner_name}"
        compile_files = [str(staged)]

        if inner_name != design_name:
            tb_code = tb_path.read_text()
            wrapper = self._generate_rtllm_wrapper(
                design_name, inner_name, sparkle_ports,
                tb_code, sv_code, info.ref_code,
            )
            if not wrapper:
                return "sim_error", -1, "Could not generate RTLLM GLS wrapper"
            (gls_dir / "wrapper.sv").write_text(wrapper)
            compile_files.append(str(gls_dir / "wrapper.sv"))

        compile_files.append(str(gls_dir / "testbench.v"))
'''
if old_rtllm not in text:
    raise SystemExit("could not find RTLLM GLS compile block")
text = text.replace(old_rtllm, new_rtllm, 1)

cvdp_fn = '''
    def _run_gls_cvdp(
        self, prob_id: str, sv_code: str,
        sparkle_mod_name: str, sparkle_ports: list[tuple[str, str, str]],
        run_dir: Path, synth_dir: Path,
        netlist_path: Path, stage: str,
    ) -> tuple[str, int, str]:
        """GLS for CVDP: renamed netlist + existing local cocotb harness."""
        if self.dataset_obj is None:
            return "sim_error", -1, "CVDP dataset object not set on evaluator"
        info = self.dataset_obj.load_problem(prob_id)
        design_name = info.design_name
        inner_name = f"{design_name}_sparkle_gls"
        gls_dir = synth_dir / f"gls_{stage}"
        gls_dir.mkdir(parents=True, exist_ok=True)
        staged = self._stage_gls_netlist(
            netlist_path,
            gls_dir,
            [sparkle_mod_name, design_name, inner_name, "TopModule"],
            inner_name,
        )
        if staged is None:
            return "sim_error", -1, f"Could not rename CVDP GLS netlist to {inner_name}"
        net_text = staged.read_text(errors="replace")
        wrapper = generate_cvdp_wrapper(
            design_name=design_name,
            sparkle_mod_name=inner_name,
            sparkle_ports=sparkle_ports,
            ref_code=info.ref_code,
            harness_files=info.metadata.get("harness_files", {}),
            sv_code=sv_code,
        )
        if wrapper:
            sv_blob = net_text.rstrip() + "\\n\\n" + wrapper + "\\n"
        else:
            sv_blob = _rename_module_declaration(net_text, inner_name, design_name)
        sky130_dir = self._ensure_sky130_cache()
        defs = gls_dir / "gls_defs.v"
        defs.write_text("`define FUNCTIONAL\\n`define UNIT_DELAY #0\\n")
        status, mismatches, detail = self._run_sim_cvdp(
            prob_id,
            sv_blob,
            inner_name,
            sparkle_ports,
            gls_dir,
            direct_top=True,
            problem_info=info,
            extra_verilog_sources=[
                defs,
                sky130_dir / SKY130_PRIMITIVES_NAME,
                sky130_dir / SKY130_CELLS_NAME,
            ],
        )
        return status, mismatches, f"GLS {stage}: {detail}"

'''

if "def _run_gls_cvdp(" not in text:
    text = text.replace(
        "    def _run_gls_verilogeval(",
        cvdp_fn + "    def _run_gls_verilogeval(",
        1,
    )

# Patch _run_sim_cvdp signature and extra sources
old_sig = '''    def _run_sim_cvdp(
        self, prob_id: str, sv_code: str,
        sparkle_mod_name: str,
        sparkle_ports: list[tuple[str, str, str]],
        run_dir: Path,
        *,
        direct_top: bool = False,
        problem_info=None,
    ) -> tuple[str, int, str]:
'''
new_sig = '''    def _run_sim_cvdp(
        self, prob_id: str, sv_code: str,
        sparkle_mod_name: str,
        sparkle_ports: list[tuple[str, str, str]],
        run_dir: Path,
        *,
        direct_top: bool = False,
        problem_info=None,
        extra_verilog_sources: list[Path] | None = None,
    ) -> tuple[str, int, str]:
'''
if old_sig not in text:
    raise SystemExit("could not find _run_sim_cvdp signature")
text = text.replace(old_sig, new_sig, 1)

old_after_sources = '''            out_path.write_text(sv_code if source == primary else context_code)

        sim_mode = os.environ.get("CVDP_SIM_MODE", "local").lower()
'''
new_after_sources = '''            out_path.write_text(sv_code if source == primary else context_code)

        if extra_verilog_sources:
            env_path = sim_dir / "src" / ".env"
            if env_path.exists():
                extras = []
                for src in extra_verilog_sources:
                    dest = sim_dir / "gls_lib" / src.name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    if not dest.exists():
                        shutil.copy2(src, dest)
                    extras.append("/code/gls_lib/" + dest.name)
                env_txt = env_path.read_text(errors="replace")
                extra_line = " ".join(extras) + " "
                env_txt = re.sub(
                    r"(?m)^(VERILOG_SOURCES\\s*=\\s*)",
                    rf"\\1{extra_line}",
                    env_txt,
                    count=1,
                )
                env_path.write_text(env_txt)

        sim_mode = os.environ.get("CVDP_SIM_MODE", "local").lower()
'''
if old_after_sources not in text:
    raise SystemExit("could not find CVDP sim_mode inject point")
text = text.replace(old_after_sources, new_after_sources, 1)

EVAL.write_text(text)
print("patched", EVAL)

# launcher load_done: retry GLS for all datasets
lt = LAUNCH.read_text()
lbak = LAUNCH.with_suffix(".py.bak_gls_retry_20260920")
if not lbak.exists():
    lbak.write_text(lt)

old_done = '''        if row.get("pnr_pass") is not True:
            continue
        if dataset not in {"cvdp", "realbench"}:
            gls = row.get("gls_synth_status")
            if gls in (None, "", "sim_error", "not_run"):
                continue
        done.add(pid)
'''
new_done = '''        if row.get("pnr_pass") is not True:
            continue
        gls = row.get("gls_pnr_status") or row.get("gls_synth_status")
        if gls in (None, "", "sim_error", "not_run"):
            continue
        done.add(pid)
'''
if old_done not in lt:
    raise SystemExit("could not patch load_done")
lt = lt.replace(old_done, new_done, 1)

old_clean = '''        for name in (
        "orfs_results",
        "orfs_logs",
        "objects",
        "logs",
        "results",
        "gds",
        "flow",
    ):
        for p in run_dir.rglob(name):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
    # keep small reports; drop fat netlists after metrics are in jsonl
    for p in run_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in {".gds", ".oas", ".odb", ".def", ".lef"}:
            try:
                p.unlink()
            except OSError:
                pass
'''
new_clean = '''        for name in (
        "orfs_logs",
        "objects",
        "logs",
        "gds",
        "flow",
    ):
        for p in run_dir.rglob(name):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
    # Keep orfs_results/*.v for GLS retry; drop fat layout files.
    for p in run_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in {".gds", ".oas", ".odb", ".def", ".lef"}:
            try:
                p.unlink()
            except OSError:
                pass
'''
if old_clean not in lt:
    raise SystemExit("could not patch cleanup")
lt = lt.replace(old_clean, new_clean, 1)
LAUNCH.write_text(lt)
print("patched", LAUNCH)
print("ok")
