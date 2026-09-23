#!/usr/bin/env python3
from pathlib import Path
import shutil
from agent.evaluator import Evaluator

src = Path("/home/sgli/work/NL2Chip_rebuttal_artifacts/2026-09-19/sparkle_unrepaired_416ec86_backend_pd/run_20260919_220620/eval/verilogeval/Prob001_zero")
dst = Path("/tmp/lvs_smoke_prob001")
if dst.exists():
    shutil.rmtree(dst)
shutil.copytree(src, dst)
sv = (dst / "synth" / "Prob001_zero" / "Prob001_zero.sv").read_text()
ev = Evaluator(
    project_root=Path("/home/sgli/work/NL2Chip_sparkle_416ec86_backend_eval_20260919"),
    enable_synth=True, enable_pnr=True, enable_drc=True, enable_lvs=True,
    enable_gls=False, dataset="verilogeval",
)
row = ev._run_pnr("Prob001_zero", sv, "TopModule", dst)
print({k: row.get(k) for k in ["pnr_pass", "drc_pass", "lvs_pass", "lvs_error", "gds_generated"]})
print("lvs_error", str(row.get("lvs_error"))[:800])
synth = dst / "synth" / "Prob001_zero"
for name in ["lvs_stdout.txt", "lvs_stderr.txt"]:
    p = synth / name
    print("FILE", name, p.exists(), p.stat().st_size if p.exists() else 0)
    if p.exists():
        print(p.read_text(errors="replace")[-1200:])
print("lvsdb", list((synth / "orfs_results").rglob("6_lvs.lvsdb"))[:5])
print("gds", bool(list((synth / "orfs_results").rglob("6_final.gds"))))
