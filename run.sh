#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# ── 默认参数 ──
LIMIT=""
RESUME=""
MODEL="claude-sonnet-4-20250514"
FILTER=""
DRY_RUN=""

usage() {
    cat <<'EOF'
用法: ./run.sh [选项]

自然语言 → Lean (Sparkle DSL) → SystemVerilog 自动翻译管线

选项:
  -l, --limit N       只处理前 N 题 (默认: 全部 146 题)
  -r, --resume        跳过已通过的题目继续
  -m, --model MODEL   指定 LLM 模型 (默认: claude-sonnet-4-20250514)
  -f, --filter REGEX  按正则过滤题目 (如: "mux|counter")
  -d, --dry-run       只构建 prompt，不调用 LLM
  -h, --help          显示帮助

示例:
  ./run.sh                      # 跑全部题目
  ./run.sh -l 5                 # 只跑前 5 题
  ./run.sh -l 10 -r             # 跑前 10 题，跳过已通过的
  ./run.sh -f "mux|counter"     # 只跑含 mux 或 counter 的题
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -l|--limit)   LIMIT="--limit $2"; shift 2 ;;
        -r|--resume)  RESUME="--resume"; shift ;;
        -m|--model)   MODEL="$2"; shift 2 ;;
        -f|--filter)  FILTER="--filter $2"; shift 2 ;;
        -d|--dry-run) DRY_RUN="--dry-run"; shift ;;
        -h|--help)    usage ;;
        *) echo "未知选项: $1"; usage ;;
    esac
done

# ── 检查依赖 ──
if ! command -v lake &>/dev/null; then
    echo "错误: 未找到 lake，请先安装 Lean 4 (elan)"
    echo "  curl https://elan-init.trycloudflare.com/ -sSf | sh"
    exit 1
fi

if ! python3 -c "import anthropic" &>/dev/null; then
    echo "错误: 未找到 anthropic 包，正在安装..."
    pip install anthropic
fi

if [ ! -f key.env ]; then
    echo "错误: 未找到 key.env，请创建并填入 API Key:"
    echo '  echo '\''ANTHROPIC_API_KEY=sk-ant-...'\'' > key.env'
    exit 1
fi

# ── 构建 Sparkle ──
echo "=== 构建 Sparkle ==="
lake build

# ── 运行自动形式化管线 ──
echo ""
echo "=== 启动 NL → Lean → Verilog 管线 ==="
echo "模型: $MODEL"
echo ""

python3 autoformalize.py \
    --model "$MODEL" \
    $LIMIT $RESUME $FILTER $DRY_RUN
