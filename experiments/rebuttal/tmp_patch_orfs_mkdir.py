#!/usr/bin/env python3
from pathlib import Path

p = Path("/home/sgli/work/NL2Chip_sparkle_416ec86_backend_eval_20260919/agent/evaluator.py")
t = p.read_text()
old = '''        volumes = [
            f"{synth_dir / 'orfs_results'}:/OpenROAD-flow-scripts/flow/results",
            f"{synth_dir / 'orfs_logs'}:/OpenROAD-flow-scripts/flow/logs",
            f"{synth_dir / 'orfs_reports'}:/OpenROAD-flow-scripts/flow/reports",
            f"{synth_dir / 'orfs_objects'}:/OpenROAD-flow-scripts/flow/objects",
        ]
'''
new = '''        for name in ("orfs_results", "orfs_logs", "orfs_reports", "orfs_objects"):
            (synth_dir / name).mkdir(parents=True, exist_ok=True)
        volumes = [
            f"{synth_dir / 'orfs_results'}:/OpenROAD-flow-scripts/flow/results",
            f"{synth_dir / 'orfs_logs'}:/OpenROAD-flow-scripts/flow/logs",
            f"{synth_dir / 'orfs_reports'}:/OpenROAD-flow-scripts/flow/reports",
            f"{synth_dir / 'orfs_objects'}:/OpenROAD-flow-scripts/flow/objects",
        ]
'''
if old not in t:
    raise SystemExit("volumes block missing")
if "for name in (\"orfs_results\", \"orfs_logs\"" not in t:
    t = t.replace(old, new, 1)
    p.write_text(t)
    print("mkdir patched")
else:
    print("mkdir already present")
