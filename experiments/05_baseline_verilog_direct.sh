#!/usr/bin/env bash
# ============================================================
# 实验 5: Baseline — 直接生成 Verilog (Pass@1, 不迭代修复)
# 遵循 VerilogEval 原始论文评测方式
# 模型: claude-sonnet-4-5-20250929
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# 激活 Python 虚拟环境
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

python3 experiments/baseline_verilog.py \
    -m claude-sonnet-4-5-20250929 \
    -w 4 \
    "$@"
