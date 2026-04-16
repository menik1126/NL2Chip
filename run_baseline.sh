#!/usr/bin/env bash
# ============================================================
# 跑 Baseline: LLM 直接生成 Verilog (Pass@1, 单次生成, 不迭代)
# 模型: claude-sonnet-4-5-20250929 (与论文一致)
# ============================================================
set -euo pipefail

cd "$(dirname "$0")"

# 激活虚拟环境
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
elif [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

python3 experiments/baseline_verilog.py \
    -m claude-sonnet-4-5-20250929 \
    -w 8 \
    --pnr \
    "$@"
