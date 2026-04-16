#!/usr/bin/env bash
# ============================================================
# 实验 1: 主实验 — 全量 VerilogEval 156 题
# 目的: 测量 Sparkle HDL 的编译通过率和仿真通过率
# 模型: claude-sonnet-4-5-20250514
# 预计时间: 较长 (156 题 × ~2-5 min/题)
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

MODEL="claude-sonnet-4-5-20250929"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "╭──────────────────────────────────────────────╮"
echo "│  实验 1: 主实验 — 全量 VerilogEval 156 题     │"
echo "│  模型: $MODEL                                 │"
echo "╰──────────────────────────────────────────────╯"

# 跑全量 156 题，含 lint + 仿真
LOG="results/exp01_main_${TIMESTAMP}.log"
./agent/run_agent.sh \
    -m "$MODEL" \
    --synth \
    > >(tee "$LOG") 2>&1

echo ""
echo "=== 实验 1 完成 ==="
echo "日志: $LOG"
echo "结果目录: results/ 下最新的 agent_run_* 目录"
