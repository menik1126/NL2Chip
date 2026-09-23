from pathlib import Path

src = Path("/home/sgli/work/nl2chip_proof_ablation_ve_hard_private_20260920/codex_chatgpt_socks.py")
dst = Path("/home/sgli/work/nl2chip_proof_ablation_cvdp12_private_20260920/codex_chatgpt_socks.py")
t = src.read_text()
t = t.replace(
    "/home/sgli/work/NL2Chip_sparkle_proof_ablation_20260919",
    "/home/sgli/work/NL2Chip_sparkle_proof_ablation_cvdp_20260920",
)
t = t.replace(
    "/home/sgli/work/nl2chip_proof_ablation_ve_hard_private_20260920",
    "/home/sgli/work/nl2chip_proof_ablation_cvdp12_private_20260920",
)
if "ve_hard" in t or "proof_ablation_20260919" in t:
    raise SystemExit("leftover paths")
dst.write_text(t)
print("wrote", dst, "bytes", len(t))
