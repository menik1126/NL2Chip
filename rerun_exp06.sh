#!/usr/bin/env bash
# ============================================================
# 重跑实验 6: 模型消融实验
# 原因: 上次因缺少环境变量导致 503 model_not_found 错误
# 修复: 设置 CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1
#       和 CLAUDE_CODE_SKIP_BEDROCK_AUTH=1
#
# 用法:
#   ./rerun_exp06.sh                  # 重跑全部 3 个模型
#   ./rerun_exp06.sh --skip sonnet-4-5  # 跳过 sonnet-4-5 (如果它的 sim_error 不是 503 问题)
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── 关键修复: 设置缺失的环境变量 ──
export CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1
export CLAUDE_CODE_SKIP_BEDROCK_AUTH=1

# ── 环境准备 ──
if [[ -f "$SCRIPT_DIR/key.env" ]]; then
    set -a; source "$SCRIPT_DIR/key.env"; set +a
fi

if [ -f "$SCRIPT_DIR/.venv/bin/activate" ]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
fi

# ── 构建 ──
echo "=== 构建 Sparkle ==="
lake build 2>&1
echo ""

# ── 运行实验 6 ──
echo "=== 重跑实验 6: 模型消融 (3 模型 × 156 题) ==="
./experiments/06_model_ablation.sh -w 8 --no-repl --skip sonnet-4-5 "$@"
