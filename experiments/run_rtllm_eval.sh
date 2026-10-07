#!/usr/bin/env bash
# ============================================================
# RTLLM 全流程评测 (Sparkle Lean 流程)
#
# 流程: NL spec → Sparkle Lean → SystemVerilog → Sim → Synth → P&R → DRC → LVS
# 数据集: RTLLM (50 题)
# 模型: claude-sonnet-4-5-20250929
#
# 用法:
#   ./experiments/run_rtllm_eval.sh                  # 全量 50 题 (编译+仿真+综合+P&R+DRC+LVS)
#   ./experiments/run_rtllm_eval.sh -l 10            # 前 10 题快速测试
#   ./experiments/run_rtllm_eval.sh -r               # 续跑 (跳过已通过)
#   ./experiments/run_rtllm_eval.sh -f "adder|counter"  # 按正则过滤
#   ./experiments/run_rtllm_eval.sh --synth-only      # 只跑到综合 (不跑 P&R)
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# ── 默认参数 ──
MODEL="claude-sonnet-4-5-20250929"
WORKERS=8
LIMIT=""
RESUME=""
FILTER=""
SYNTH_ONLY=false
EXTRA_ARGS=""
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# ── 解析参数 ──
while [[ $# -gt 0 ]]; do
    case "$1" in
        -m|--model)       MODEL="$2"; shift 2 ;;
        -w|--workers)     WORKERS="$2"; shift 2 ;;
        -l|--limit)       LIMIT="$2"; shift 2 ;;
        -r|--resume)      RESUME="--resume"; shift ;;
        -f|--filter)      FILTER="$2"; shift 2 ;;
        --synth-only|--no-pnr) SYNTH_ONLY=true; shift ;;
        *)                EXTRA_ARGS="$EXTRA_ARGS $1"; shift ;;
    esac
done

# ── 环境准备 ──
if [[ -f "$PROJECT_ROOT/key.env" ]]; then
    set -a; source "$PROJECT_ROOT/key.env"; set +a
fi

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "错误: ANTHROPIC_API_KEY 未设置"
    echo "  请在 key.env 中添加: ANTHROPIC_API_KEY=sk-ant-..."
    exit 1
fi

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

# ── 检查依赖 ──
echo "╭──────────────────────────────────────────────────────╮"
echo "│  RTLLM 全流程评测 (Sparkle Lean)                      │"
echo "│  数据集: RTLLM (50 题)                                │"
echo "│  模型: $MODEL"
echo "│  并行度: $WORKERS workers"
echo "╰──────────────────────────────────────────────────────╯"
echo ""

# 检查数据集
DATASET_DIR="$PROJECT_ROOT/RTLLM"
if [[ ! -d "$DATASET_DIR" ]]; then
    echo "错误: 数据集目录不存在: $DATASET_DIR"
    echo "  请先 clone RTLLM: git clone https://github.com/hkust-zhiyao/RTLLM.git"
    echo "  并固定到 v2.0: git -C RTLLM checkout 41b2689"
    exit 1
fi

PROB_COUNT=$(find "$DATASET_DIR" -name "design_description.txt" -not -path "*/_*" | wc -l)
echo "  数据集: $PROB_COUNT 题 (RTLLM)"

if ! command -v lake &>/dev/null; then
    echo "错误: 未找到 lake，请先安装 Lean 4 (elan)"
    exit 1
fi

if ! python3 -c "import anthropic" &>/dev/null; then
    echo "错误: 未找到 anthropic 包，请运行: uv sync"
    exit 1
fi

if ! $SYNTH_ONLY; then
    if ! docker info >/dev/null 2>&1; then
        echo "错误: P&R/DRC/LVS 需要 Docker，但 Docker 未运行"
        echo "  如果只需要综合，请加 --synth-only 参数"
        exit 1
    fi
    echo "  Docker: $(docker --version 2>/dev/null | head -1)"
fi

echo "  Python: $(which python3)"
echo "  lake:   $(command -v lake)"
echo "  iverilog: $(command -v iverilog 2>/dev/null || echo '未安装')"
echo ""

# ── 构建 Sparkle ──
echo "=== 构建 Sparkle ==="
lake build
echo ""

# ── 构建 agent 参数 ──
AGENT_ARGS="-m $MODEL -w $WORKERS --dataset rtllm"

if $SYNTH_ONLY; then
    AGENT_ARGS="$AGENT_ARGS --synth"
else
    AGENT_ARGS="$AGENT_ARGS --pnr --drc --lvs"
fi

if [[ -n "$LIMIT" ]]; then
    AGENT_ARGS="$AGENT_ARGS -l $LIMIT"
fi

if [[ -n "$RESUME" ]]; then
    AGENT_ARGS="$AGENT_ARGS $RESUME"
fi

if [[ -n "$FILTER" ]]; then
    AGENT_ARGS="$AGENT_ARGS -f $FILTER"
fi

mkdir -p results

# ============================================================
# 主实验 — 编译 + 仿真 + 综合 (+ P&R + DRC + LVS)
# ============================================================
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  RTLLM 评测 (编译 + 仿真 + 综合"
if $SYNTH_ONLY; then
    echo "           综合模式: 只跑到 Yosys 综合)"
else
    echo "           + P&R + DRC + LVS)"
fi
echo "  开始: $(date '+%Y-%m-%d %H:%M:%S')"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

LOG_MAIN="results/rtllm_main_${TIMESTAMP}.log"
START_TIME=$(date +%s)

python3 agent/search.py $AGENT_ARGS $EXTRA_ARGS 2>&1 | tee "$LOG_MAIN"

MAIN_ELAPSED=$(( $(date +%s) - START_TIME ))

# ============================================================
# 汇总
# ============================================================
HOURS=$(( MAIN_ELAPSED / 3600 ))
MINS=$(( (MAIN_ELAPSED % 3600) / 60 ))
SECS=$(( MAIN_ELAPSED % 60 ))

echo ""
echo "╭──────────────────────────────────────────────────────╮"
echo "│  RTLLM 评测完成!                                      │"
echo "│  总耗时: ${HOURS}h ${MINS}m ${SECS}s"
echo "│  结果目录: results/ 下最新的 agent_run_* 目录"
echo "│  日志: $LOG_MAIN"
echo "╰──────────────────────────────────────────────────────╯"
echo ""
echo "查看结果:"
echo "  cat results/agent_run_*/summary.json"
echo "  python3 agent/report.py results/agent_run_*"
