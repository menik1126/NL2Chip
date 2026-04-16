#!/usr/bin/env bash
# ============================================================
# 对主表三个实验补跑 DRC + LVS
#
# 用法:
#   ./experiments/run_drc_lvs_all.sh              # 跑全部三个
#   ./experiments/run_drc_lvs_all.sh --skip-pnr   # 已有 P&R 时跳过
#   ./experiments/run_drc_lvs_all.sh --drc-only    # 只跑 DRC
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

if ! docker info >/dev/null 2>&1; then
    echo "错误: DRC/LVS 需要 Docker"
    exit 1
fi

# 三个主实验的结果目录
SPARKLE_RUN="results/agent_run_20260413_035544"        # Sparkle (compile=144, sim=113, pnr=142)
SPARKLE_SYNTH_RUN="results/agent_run_20260413_025552"  # Sparkle synth (compile=145, synth=144, pnr=0)
BASELINE_RUN="results/baseline_verilog_20260415_191611" # Baseline (compile=140, sim=112, pnr=106)

EXTRA_ARGS="${*}"

echo "╭──────────────────────────────────────────────────╮"
echo "│  补跑 DRC + LVS (主表三个实验)                    │"
echo "╰──────────────────────────────────────────────────╯"
echo ""

for RUN in "$SPARKLE_RUN" "$SPARKLE_SYNTH_RUN" "$BASELINE_RUN"; do
    if [ ! -d "$RUN" ]; then
        echo "[跳过] $RUN 不存在"
        continue
    fi
    if [ ! -d "$RUN/synth" ]; then
        echo "[跳过] $RUN/synth 不存在"
        continue
    fi

    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  处理: $RUN"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    python3 experiments/drc_lvs_reuse.py --run-dir "$RUN" --skip-pnr $EXTRA_ARGS
    echo ""
done

echo "全部完成!"
