#!/usr/bin/env python3
from pathlib import Path

evalp = Path("/home/sgli/work/NL2Chip_sparkle_416ec86_backend_eval_20260919/agent/evaluator.py")
text = evalp.read_text()
bak = evalp.with_suffix(".py.bak_lvs_cdl_20260920")
if not bak.exists():
    bak.write_text(text)

old = '''    def _run_lvs(synth_dir: Path, volumes: list[str]) -> dict:
        """Run KLayout LVS, fixing sky130hd CDL 'short' keyword that KLayout can't parse."""
        result: dict = {"lvs_pass": None, "lvs_error": None}

        # sky130 CDL has two issues KLayout 0.30.x can't handle:
        # 1. "rXX ... short" resistor lines (zero-ohm connections) — delete them
        # 2. "/" separator between pins and subcircuit name — remove it
        lvs_cmd = (
            "sed -i -e '/ short$/d' -e 's| / | |g' "
            "/OpenROAD-flow-scripts/flow/platforms/sky130hd/cdl/sky130hd.cdl && "
            "make DESIGN_CONFIG=/workspace/config.mk lvs"
        )
'''
new = '''    def _run_lvs(synth_dir: Path, volumes: list[str]) -> dict:
        """Run KLayout LVS with a writable, patched sky130hd CDL copy."""
        result: dict = {"lvs_pass": None, "lvs_error": None}

        # Image CDL is root-owned; --user cannot sed -i it. Copy to /workspace first.
        # sky130 CDL issues for KLayout 0.30.x:
        # 1. "rXX ... short" resistor lines (zero-ohm connections) — delete them
        # 2. "/" separator between pins and subcircuit name — remove it
        lvs_cmd = (
            "cp /OpenROAD-flow-scripts/flow/platforms/sky130hd/cdl/sky130hd.cdl "
            "/workspace/sky130hd_lvs.cdl && "
            "sed -i -e '/ short$/d' -e 's| / | |g' /workspace/sky130hd_lvs.cdl && "
            "make DESIGN_CONFIG=/workspace/config.mk "
            "CDL_FILE=/workspace/sky130hd_lvs.cdl lvs"
        )
'''
if old not in text:
    raise SystemExit("lvs block not found")
text = text.replace(old, new, 1)

old_end = '''        if lvs_result.get("success"):
            result["lvs_pass"] = True
        else:
            stderr = lvs_result.get("stderr", "")
            if "Can't find a value for a R, C or L device" in stderr:
                result["lvs_error"] = "KLayout CDL parse error (platform issue)"
            else:
                result["lvs_pass"] = False
                result["lvs_error"] = stderr[-200:]

        return result
'''
new_end = '''        stdout = lvs_result.get("stdout") or ""
        stderr = lvs_result.get("stderr") or ""
        if isinstance(stdout, (bytes, bytearray)):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, (bytes, bytearray)):
            stderr = stderr.decode("utf-8", errors="replace")
        (synth_dir / "lvs_stdout.txt").write_text(stdout[-200000:], encoding="utf-8")
        (synth_dir / "lvs_stderr.txt").write_text(stderr[-200000:], encoding="utf-8")
        lvsdb = list((synth_dir / "orfs_results").rglob("6_lvs.lvsdb"))
        if lvs_result.get("success") and lvsdb:
            result["lvs_pass"] = True
        else:
            combined = stdout + "\\n" + stderr
            if "Can't find a value for a R, C or L device" in combined:
                result["lvs_error"] = "KLayout CDL parse error (platform issue)"
                result["lvs_pass"] = False
            else:
                result["lvs_pass"] = False
                result["lvs_error"] = (stderr or stdout)[-400:]

        return result
'''
if old_end not in text:
    raise SystemExit("lvs end not found")
text = text.replace(old_end, new_end, 1)
evalp.write_text(text)
print("evaluator patched")

runner = Path("/home/sgli/work/NL2Chip_sparkle_416ec86_backend_eval_20260919/agent/orfs_runner.py")
rt = runner.read_text()
rbak = runner.with_suffix(".py.bak_lvs_cdl_20260920")
if not rbak.exists():
    rbak.write_text(rt)
old_run = '''        proc = subprocess.run(
            docker_command,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
'''
new_run = '''        proc = subprocess.run(
            docker_command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
'''
if old_run not in rt:
    raise SystemExit("orfs_runner run block missing")
runner.write_text(rt.replace(old_run, new_run, 1))
print("orfs_runner patched")
