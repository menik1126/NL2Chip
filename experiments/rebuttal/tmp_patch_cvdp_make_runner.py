from pathlib import Path

p = Path("/home/sgli/work/NL2Chip_sparkle_proof_ablation_cvdp_20260920/cktarchon/run.py")
t = p.read_text()
old = """    if plan_payload:
        required_modules = tuple(
            row["module_name"] for row in plan_payload.get("cases", [])
        )
    elif native_payload and getattr(info, "design_name", None):
        required_modules = (str(info.design_name),)
    else:
        required_modules = ()
"""
new = """    if plan_payload:
        required_modules = tuple(
            row["module_name"] for row in plan_payload.get("cases", [])
        )
    elif getattr(info, "design_name", None):
        # CVDP harness DUT name (and VE TopModule) must appear in extracted SV.
        required_modules = (str(info.design_name),)
    else:
        required_modules = ()
"""
if old not in t:
    raise SystemExit("block missing")
p.write_text(t.replace(old, new, 1))
print("patched make_runner")
