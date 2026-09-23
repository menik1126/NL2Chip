#!/usr/bin/env python3
from pathlib import Path

ev = Path("/home/sgli/work/NL2Chip_sparkle_416ec86_backend_eval_20260919/agent/evaluator.py")
text = ev.read_text()
bak = ev.with_suffix(".py.bak_gls_pnr_20260920")
if not bak.exists():
    bak.write_text(text)

old_to = "PNR_TIMEOUT = 900"
if old_to not in text:
    raise SystemExit("PNR_TIMEOUT marker missing")
text = text.replace(old_to, "PNR_TIMEOUT = 3600", 1)

old_die = """        if cell_area and cell_area > 0:
            # Target ~30% utilization, with minimum die size for PDN
            core_side = math.sqrt(cell_area / 0.3)
            die_side = max(core_side + 4, MIN_DIE_SIDE_UM)
        else:
            die_side = MIN_DIE_SIDE_UM

        die_side = math.ceil(die_side)"""
new_die = """        if cell_area and cell_area > 0:
            # Loose floorplan: 32-wide GPL was stalling on overflow~0.75 in 50x50.
            core_side = math.sqrt(cell_area / 0.15)
            die_side = max(core_side + 10, MIN_DIE_SIDE_UM, 80)
        else:
            die_side = max(MIN_DIE_SIDE_UM, 80)

        die_side = math.ceil(die_side)"""
if old_die not in text:
    raise SystemExit("die_area block missing")
text = text.replace(old_die, new_die, 1)

old_disp = """        if self.dataset_name in {"rtllm", "resbench"}:
            status, mismatches, detail = self._run_gls_rtllm(
                prob_id, sv_code, sparkle_mod_name, sparkle_ports,
                run_dir, synth_dir, netlist_path, stage,
            )
        else:"""
new_disp = """        if self.dataset_name == "resbench":
            status, mismatches, detail = self._run_gls_resbench(
                prob_id, sv_code, sparkle_mod_name, sparkle_ports,
                run_dir, synth_dir, netlist_path, stage,
            )
        elif self.dataset_name == "rtllm":
            status, mismatches, detail = self._run_gls_rtllm(
                prob_id, sv_code, sparkle_mod_name, sparkle_ports,
                run_dir, synth_dir, netlist_path, stage,
            )
        else:"""
if old_disp not in text:
    raise SystemExit("gls dispatch missing")
text = text.replace(old_disp, new_disp, 1)

marker = "    def _run_gls_rtllm("
insert = '''    def _run_gls_resbench(
        self, prob_id: str, sv_code: str,
        sparkle_mod_name: str, sparkle_ports: list[tuple[str, str, str]],
        run_dir: Path, synth_dir: Path,
        netlist_path: Path, stage: str,
    ) -> tuple[str, int, str]:
        """GLS for ResBench: embedded TB from problems.json metadata, not the catalog file."""
        sky130_dir = self._ensure_sky130_cache()
        if self.dataset_obj is None:
            return "sim_error", -1, "ResBench dataset object not set on evaluator"
        info = self.dataset_obj.load_problem(prob_id)
        testbench = info.metadata.get("testbench", "") or ""
        if not testbench.strip():
            return "sim_error", -1, "ResBench testbench missing from metadata"
        design_name = info.design_name

        gls_dir = synth_dir / f"gls_{stage}"
        gls_dir.mkdir(parents=True, exist_ok=True)
        tb_path = gls_dir / "testbench.sv"
        tb_path.write_text(testbench)

        bridged_sv = sv_code
        if sparkle_mod_name and sparkle_mod_name != design_name:
            bridged_sv = re.sub(
                rf"\\bmodule\\s+{re.escape(sparkle_mod_name)}\\b",
                f"module {design_name}",
                sv_code,
                count=1,
            )

        from resbench_port_bridge import build_bridge
        _inner_sv, wrapper = build_bridge(
            design_name,
            bridged_sv,
            testbench,
            parse_module_ports,
            _ports_equivalent,
            _is_reset_like,
        )

        net_text = netlist_path.read_text(errors="replace")
        netlist_staged = gls_dir / "netlist.v"
        inner_name = f"{design_name}_sparkle"
        if wrapper:
            renamed = False
            for top in (design_name, sparkle_mod_name, inner_name):
                if not top:
                    continue
                new_text, n = re.subn(
                    rf"\\bmodule\\s+{re.escape(top)}\\b",
                    f"module {inner_name}",
                    net_text,
                    count=1,
                )
                if n:
                    net_text = new_text
                    renamed = True
                    break
            if not renamed:
                return "sim_error", -1, f"Could not rename netlist top to {inner_name}"
            (gls_dir / "wrapper.sv").write_text(wrapper)
        elif sparkle_mod_name and sparkle_mod_name != design_name:
            new_text, n = re.subn(
                rf"\\bmodule\\s+{re.escape(sparkle_mod_name)}\\b",
                f"module {design_name}",
                net_text,
                count=1,
            )
            if n:
                net_text = new_text
        netlist_staged.write_text(net_text)

        compile_files = [
            str(sky130_dir / SKY130_PRIMITIVES_NAME),
            str(sky130_dir / SKY130_CELLS_NAME),
            str(netlist_staged),
        ]
        if wrapper:
            compile_files.append(str(gls_dir / "wrapper.sv"))
        compile_files.append(str(tb_path))

        vvp_file = gls_dir / "gls.vvp"
        compile_cmd = [
            "iverilog", "-g2012", "-DFUNCTIONAL", "-DUNIT_DELAY=#0",
            "-o", str(vvp_file),
            *compile_files,
        ]
        try:
            comp = subprocess.run(
                compile_cmd, capture_output=True, text=True, timeout=GLS_TIMEOUT,
            )
            if comp.returncode != 0:
                (gls_dir / "gls_compile_error.txt").write_text(comp.stderr)
                return "sim_error", -1, f"GLS compile failed:\\n{comp.stderr[:500]}"
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
        if RESBENCH_PASS_PATTERN.search(sim_output):
            return "sim_pass", 0, f"GLS {stage}: Design passed"
        if "Some tests failed" in sim_output or "FAIL" in sim_output:
            return "sim_fail", -1, f"GLS {stage}: Design did not pass:\\n{sim_output[:300]}"
        return "sim_error", -1, f"GLS {stage}: could not parse output:\\n{sim_output[:300]}"

'''
if "def _run_gls_resbench(" in text:
    raise SystemExit("already patched _run_gls_resbench")
if marker not in text:
    raise SystemExit("rtllm marker missing")
# The insert string used doubled backslashes for writing this file; decode them
# into real regex escapes when embedding into evaluator.py.
insert = insert.replace("\\\\b", "\\b").replace("\\\\s", "\\s").replace("\\\\n", "\\n")
text = text.replace(marker, insert + marker, 1)

ev.write_text(text)
print("evaluator patched", ev.stat().st_size)

lp = Path("/home/sgli/work/nl2chip_sparkle_416ec86_backend_eval_private_20260919/launch_backend_pd.py")
lt = lp.read_text()
lp.with_suffix(".py.bak_gls_pnr_20260920").write_text(lt)
old_ld = '''def load_done(path: Path) -> set[str]:
    """Last-wins. Retry infrastructure crashes; keep real PD rows."""
    last: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return set()
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        pid = row.get("prob_id")
        if pid:
            last[pid] = row
    done: set[str] = set()
    for pid, row in last.items():
        if row.get("failure_stage") == "infrastructure":
            continue
        if row.get("synth_attempted"):
            done.add(pid)
    return done
'''
new_ld = '''def load_done(path: Path, dataset: str = "") -> set[str]:
    """Last-wins. Retry infra, incomplete P&R, and GLS sim_error (not synth-only)."""
    last: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return set()
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        pid = row.get("prob_id")
        if pid:
            last[pid] = row
    done: set[str] = set()
    for pid, row in last.items():
        if row.get("failure_stage") == "infrastructure":
            continue
        if not row.get("synth_attempted"):
            continue
        if not row.get("synth_pass"):
            done.add(pid)
            continue
        if row.get("pnr_pass") is not True:
            continue
        if dataset not in {"cvdp", "realbench"}:
            gls = row.get("gls_synth_status")
            if gls in (None, "", "sim_error", "not_run"):
                continue
        done.add(pid)
    return done
'''
if old_ld not in lt:
    raise SystemExit("load_done block missing")
lt = lt.replace(old_ld, new_ld, 1)
if "done = load_done(jsonl, ds)" not in lt:
    lt = lt.replace("done = load_done(jsonl)", "done = load_done(jsonl, ds)", 1)
lp.write_text(lt)
print("launcher patched")
