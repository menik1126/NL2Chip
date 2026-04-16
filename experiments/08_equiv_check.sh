#!/usr/bin/env bash
# ============================================================
# Equiv Check: 对 baseline 仿真通过的设计做 Yosys 形式化等价验证
# 找出「仿真通过但功能不等价」的假阳性
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

python3 experiments/equiv_check.py "$@"
