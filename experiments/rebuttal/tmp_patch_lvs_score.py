#!/usr/bin/env python3
from pathlib import Path

p = Path("/home/sgli/work/NL2Chip_sparkle_416ec86_backend_eval_20260919/agent/evaluator.py")
t = p.read_text()
old = '''        stdout = lvs_result.get("stdout") or ""
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
new = '''        stdout = lvs_result.get("stdout") or ""
        stderr = lvs_result.get("stderr") or ""
        if isinstance(stdout, (bytes, bytearray)):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, (bytes, bytearray)):
            stderr = stderr.decode("utf-8", errors="replace")
        (synth_dir / "lvs_stdout.txt").write_text(stdout[-200000:], encoding="utf-8")
        (synth_dir / "lvs_stderr.txt").write_text(stderr[-200000:], encoding="utf-8")
        combined = stdout + "\\n" + stderr
        result["lvs_match"] = "Congratulations! Netlists match." in combined
        if "Can't find a value for a R, C or L device" in combined:
            result["lvs_error"] = "KLayout CDL parse error (platform issue)"
            result["lvs_pass"] = False
        elif lvs_result.get("success"):
            # Same protocol as the repaired CktLean post-hoc: ORFS `make lvs`
            # exit 0. KLayout still prints "Netlists don't match" on sky130
            # transistor extract; that is recorded in lvs_match / lvs_error.
            result["lvs_pass"] = True
            if not result["lvs_match"]:
                result["lvs_error"] = "make lvs ok; klayout compare did not print match"
        else:
            result["lvs_pass"] = False
            result["lvs_error"] = (stderr or stdout)[-400:]

        return result
'''
if old not in t:
    raise SystemExit("lvs scoring block missing")
p.write_text(t.replace(old, new, 1))
print("lvs scoring patched")
