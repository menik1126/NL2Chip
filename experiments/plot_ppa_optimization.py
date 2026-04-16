#!/usr/bin/env python3
"""Generate PPA optimization trajectory plot for the paper."""
import json
from pathlib import Path
import matplotlib.pyplot as plt
import matplotlib
matplotlib.rcParams.update({
    "font.family": "serif",
    "font.size": 10,
    "axes.labelsize": 11,
    "legend.fontsize": 8.5,
    "figure.dpi": 300,
})

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
RUN_DIR = PROJECT_ROOT / "results" / "agent_run_20260413_233820"

rf = RUN_DIR / "results.jsonl"
trajectories = {}
for line in rf.read_text().splitlines():
    r = json.loads(line)
    hist = r.get("ppa_history")
    if not hist or len(hist) < 2:
        continue
    pid = r["prob_id"]
    areas = [h.get("area_um2") for h in hist]
    if any(a is None for a in areas):
        continue
    # Only include problems where area actually changed
    if areas[0] != areas[-1]:
        # Normalize to relative change from iter 0
        base = areas[0]
        rel = [(a / base) for a in areas]
        trajectories[pid] = {"areas": areas, "rel": rel, "n": len(areas)}

# Sort by final reduction (best improvement first)
sorted_pids = sorted(trajectories, key=lambda p: trajectories[p]["rel"][-1])

# --- Plot 1: Normalized area trajectories ---
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))

# Pick top improving + some non-improving for contrast
improving = [p for p in sorted_pids if trajectories[p]["rel"][-1] < 0.95]
others = [p for p in sorted_pids if trajectories[p]["rel"][-1] >= 0.95]

colors_imp = plt.cm.Blues([ 0.5 + 0.5 * i / max(len(improving)-1, 1) for i in range(len(improving))])
colors_oth = plt.cm.Reds([0.3 + 0.4 * i / max(len(others)-1, 1) for i in range(len(others))])

for i, pid in enumerate(improving):
    t = trajectories[pid]
    label = pid.split("_", 1)[1] if "_" in pid else pid
    ax1.plot(range(len(t["rel"])), t["rel"], "o-", color=colors_imp[i],
             markersize=4, linewidth=1.5, label=label)

for i, pid in enumerate(others[:5]):  # limit non-improving to 5
    t = trajectories[pid]
    label = pid.split("_", 1)[1] if "_" in pid else pid
    ax1.plot(range(len(t["rel"])), t["rel"], "s--", color=colors_oth[i],
             markersize=3, linewidth=1, alpha=0.6, label=label)

ax1.axhline(y=1.0, color="gray", linestyle=":", linewidth=0.8)
ax1.set_xlabel("Optimization Iteration")
ax1.set_ylabel("Relative Area (normalized to iter 0)")
ax1.set_title("(a) Area Trajectory per Design")
ax1.legend(loc="upper right", ncol=2, framealpha=0.9)
ax1.set_ylim(0.55, 1.25)

# --- Plot 2: Best area reduction bar chart ---
reductions = []
for pid in sorted_pids:
    t = trajectories[pid]
    best_rel = min(t["rel"])
    reduction_pct = (1 - best_rel) * 100
    label = pid.split("_", 1)[1] if "_" in pid else pid
    reductions.append((label, reduction_pct, t["areas"][0], min(t["areas"])))

# Sort by reduction
reductions.sort(key=lambda x: x[1], reverse=True)

labels = [r[0] for r in reductions]
vals = [r[1] for r in reductions]
bar_colors = ["#2166ac" if v > 5 else "#d6604d" if v < 0 else "#999999" for v in vals]

bars = ax2.barh(range(len(labels)), vals, color=bar_colors, edgecolor="white", linewidth=0.5)
ax2.set_yticks(range(len(labels)))
ax2.set_yticklabels(labels, fontsize=7.5)
ax2.set_xlabel("Best Area Reduction (%)")
ax2.set_title("(b) Best Reduction Achieved")
ax2.axvline(x=5, color="gray", linestyle="--", linewidth=0.8, label=r"$\tau=5\%$ threshold")
ax2.legend(fontsize=8)
ax2.invert_yaxis()

plt.tight_layout()
out_path = PROJECT_ROOT / "paper" / "img" / "ppa_optimization.pdf"
out_path.parent.mkdir(parents=True, exist_ok=True)
plt.savefig(out_path, bbox_inches="tight")
print(f"Saved to {out_path}")

# Also save to the copy
out_path2 = PROJECT_ROOT / "69d7a86af51b3544c54893a8" / "xiongjing" / "sparkle" / "paper" / "img" / "ppa_optimization.pdf"
if out_path2.parent.exists():
    plt.savefig(out_path2, bbox_inches="tight")
    print(f"Saved to {out_path2}")

# Print summary stats
print(f"\nTotal designs with area changes: {len(trajectories)}")
print(f"Designs with >5% reduction: {len(improving)}")
print(f"\nTop reductions:")
for label, pct, orig, best in reductions[:10]:
    print(f"  {label}: {orig:.1f} -> {best:.1f} um2 ({pct:.1f}%)")
