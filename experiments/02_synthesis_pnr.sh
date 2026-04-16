#!/usr/bin/env bash
# ============================================================
# 实验 2: 物理综合 + Place & Route
# 目的: 对全量通过仿真的设计做 Synthesis + P&R，收集 PPA 数据
# 模型: claude-sonnet-4-5-20250514
# 需要: Docker (OpenROAD/ORFS)
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

MODEL="claude-sonnet-4-5-20250929"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "╭──────────────────────────────────────────────╮"
echo "│  实验 2: Synthesis + Place & Route            │"
echo "│  模型: $MODEL                                 │"
echo "╰──────────────────────────────────────────────╯"

# 全量 156 题 + 综合 + P&R + 多角 STA
LOG="results/exp02_pnr_${TIMESTAMP}.log"
./agent/run_agent.sh \
    -m "$MODEL" \
    --pnr \
    --corners \
    > >(tee "$LOG") 2>&1

echo ""
echo "=== 实验 2 完成 ==="
echo "日志: $LOG"
