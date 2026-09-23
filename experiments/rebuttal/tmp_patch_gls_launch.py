from pathlib import Path

p = Path("/home/sgli/work/nl2chip_sparkle_416ec86_backend_eval_private_20260919/launch_backend_pd.py")
t = p.read_text()
t2 = t.replace(
    '        if dataset not in {"cvdp", "realbench"}:\n            gls = row.get("gls_synth_status")\n            if gls in (None, "", "sim_error", "not_run"):\n                continue\n        done.add(pid)\n',
    '        gls = row.get("gls_pnr_status") or row.get("gls_synth_status")\n        if gls in (None, "", "sim_error", "not_run"):\n            continue\n        done.add(pid)\n',
    1,
)
if t2 == t:
    raise SystemExit("load_done replace failed")
t = t2
if '"orfs_results",\n' not in t:
    raise SystemExit("orfs_results string missing")
t = t.replace('        "orfs_results",\n', "", 1)
p.write_text(t)
print("launch patched")
print("has cvdp skip", 'dataset not in {"cvdp"' in t)
print("has orfs_results in cleanup", '"orfs_results"' in t.split("def cleanup_run_dir")[1][:400])
