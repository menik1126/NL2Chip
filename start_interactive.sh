#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── 激活 Python 虚拟环境 ──
if [ -f "$SCRIPT_DIR/.venv/bin/activate" ]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
fi

# ── API key ──
if [[ -f "$SCRIPT_DIR/key.env" ]]; then
    set -a; source "$SCRIPT_DIR/key.env"; set +a
fi

# ── 模式选择 ──
MODE=""
AGENT_ARGS=()
INTERACTIVE_ARGS=()

usage() {
    cat <<'EOF'
用法: ./start_interactive.sh [模式] [选项]

=== 模式 ===
  repl                  手动交互式 REPL（默认）
  agent                 Agent 自动设计芯片（调用 LLM coding agent）

=== REPL 模式选项 ===
  <file.lean>           加载文件后进入 REPL
  --watch, -w           监听文件变化自动验证
  --timeout, -t N       验证超时秒数（默认 120）

=== Agent 模式选项（同 agent/run_agent.sh）===
  -l, --limit N         只处理前 N 题
  -r, --resume          续跑（跳过已通过）
  -f, --filter REGEX    按正则过滤题目
  -m, --model MODEL     指定模型（默认 claude-sonnet-4-20250514）
  --synth               综合 + PPA
  --pnr                 综合 + 布局布线
  --drc                 DRC 检查
  --lvs                 LVS 检查
  --ppa-opt             PPA 优化循环
  --arch-explore        架构探索
  --corners             多角 PVT STA

=== 示例 ===
  ./start_interactive.sh                          # 手动 REPL
  ./start_interactive.sh repl my_chip.lean        # 加载文件进 REPL
  ./start_interactive.sh agent -l 5               # Agent 跑前 5 题
  ./start_interactive.sh agent -l 3 --synth       # Agent 跑 3 题 + 综合
  ./start_interactive.sh agent -f "mux" --pnr     # Agent 跑 mux 题 + P&R
EOF
    exit 0
}

# 解析第一个参数判断模式
if [[ $# -gt 0 ]]; then
    case "$1" in
        -h|--help) usage ;;
        repl)
            MODE="repl"
            shift
            INTERACTIVE_ARGS=("$@")
            ;;
        agent)
            MODE="agent"
            shift
            AGENT_ARGS=("$@")
            ;;
        *)
            # 没指定模式，默认 repl，所有参数传给 interactive_lean.py
            MODE="repl"
            INTERACTIVE_ARGS=("$@")
            ;;
    esac
else
    MODE="repl"
fi

# ── 检查依赖 ──
echo "╭──────────────────────────────────────────╮"
echo "│  Sparkle 交互式芯片设计                  │"
echo "╰──────────────────────────────────────────╯"
echo ""

if ! command -v lake &>/dev/null; then
    echo "错误: 未找到 lake，请先安装 Lean 4 (elan)"
    echo "  curl https://elan-init.trycloudflare.com/ -sSf | sh"
    exit 1
fi

echo "  模式:     $MODE"
echo "  lake:     $(command -v lake)"
echo "  Python:   $(which python3)"
echo "  iverilog: $(command -v iverilog 2>/dev/null || echo '未安装')"
echo ""

# ── 确保 Sparkle 已编译 ──
echo "=== 构建 Sparkle ==="
lake build
echo ""

# ── 启动 ──
if [[ "$MODE" == "repl" ]]; then
    echo "=== 启动交互式 REPL ==="
    exec python3 "$SCRIPT_DIR/interactive_lean.py" "${INTERACTIVE_ARGS[@]}"
else
    # Agent 模式：检查 API key
    if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
        echo "错误: ANTHROPIC_API_KEY 未设置"
        echo "  请在 key.env 中添加: ANTHROPIC_API_KEY=sk-ant-..."
        exit 1
    fi

    if ! python3 -c "import anthropic" &>/dev/null; then
        echo "错误: 未找到 anthropic 包，请运行: uv sync"
        exit 1
    fi

    # Docker check for synth/pnr/drc/lvs
    if echo "${AGENT_ARGS[*]}" | grep -qE -- "--(synth|pnr|drc|lvs|arch-explore|corners)"; then
        if ! docker info >/dev/null 2>&1; then
            echo "错误: --synth/--pnr 需要 Docker，但 Docker 未运行"
            exit 1
        fi
        echo "  Docker: $(docker --version 2>/dev/null | head -1)"
    fi

    echo "=== 启动 Coding Agent ==="
    exec python3 "$SCRIPT_DIR/agent/search.py" "${AGENT_ARGS[@]}"
fi
