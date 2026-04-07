#!/usr/bin/env bash
# Sparkle Coding Agent — NL → Lean → Verilog pipeline via LLM coding agent.
#
# Usage:
#   ./agent/run_agent.sh                    # 全部 146 题
#   ./agent/run_agent.sh -l 5               # 前 5 题
#   ./agent/run_agent.sh -r                  # 续跑 (跳过已通过)
#   ./agent/run_agent.sh -f "mux|counter"    # 按正则过滤
#   ./agent/run_agent.sh -m claude-sonnet-4-20250514  # 指定模型
#   ./agent/run_agent.sh -l 5 --synth        # 跑 5 题 + 综合/PPA
#   ./agent/run_agent.sh -l 5 --pnr          # 跑 5 题 + 综合 + P&R
#   ./agent/run_agent.sh -l 5 --drc          # 跑 5 题 + 综合 + P&R + DRC
#   ./agent/run_agent.sh -l 5 --lvs          # 跑 5 题 + 综合 + P&R + LVS
#   ./agent/run_agent.sh -l 5 --drc --lvs    # 跑 5 题 + 综合 + P&R + DRC + LVS

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# ── API key ──────────────────────────────────────────────────
if [[ -f "$PROJECT_ROOT/key.env" ]]; then
    set -a; source "$PROJECT_ROOT/key.env"; set +a
fi

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "错误: ANTHROPIC_API_KEY 未设置"
    echo "  请在 key.env 中添加: ANTHROPIC_API_KEY=sk-ant-..."
    exit 1
fi

# ── 激活 Python 虚拟环境 (sparkle 自己的 uv 环境) ──
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

# ── 检查依赖 ──
echo "╭─────────────────────────────────────────────╮"
echo "│  Sparkle Coding Agent — NL → Lean → Verilog │"
echo "╰─────────────────────────────────────────────╯"
echo ""

if ! command -v lake &>/dev/null; then
    echo "错误: 未找到 lake，请先安装 Lean 4 (elan)"
    exit 1
fi

if ! python3 -c "import anthropic" &>/dev/null; then
    echo "错误: 未找到 anthropic 包，请运行: uv sync"
    exit 1
fi

# Docker check (only when --synth, --pnr, --drc, or --lvs is used)
if echo "$@" | grep -qE -- "--(synth|pnr|drc|lvs)"; then
    if ! docker info >/dev/null 2>&1; then
        echo "错误: --synth/--pnr 需要 Docker，但 Docker 未运行"
        exit 1
    fi
    echo "  Docker:   $(docker --version 2>/dev/null | head -1)"
fi

echo "  Python:   $(which python3)"
echo "  lake:     $(command -v lake)"
echo "  iverilog: $(command -v iverilog 2>/dev/null || echo '未安装')"
echo ""

# ── 构建 Sparkle ──
echo "=== 构建 Sparkle ==="
lake build
echo ""

# ── 运行 Agent ──
echo "=== 启动 Coding Agent ==="
exec python3 agent/search.py "$@"
