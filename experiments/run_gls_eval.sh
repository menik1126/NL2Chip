#!/usr/bin/env bash
# ============================================================
# Gate-Level Simulation (GLS) 功能验证评测
#
# 对 Sparkle (我们的方法) 和 Baseline (直接 Verilog) 都跑:
#   RTL sim → Synth → Post-synth GLS → P&R → Post-PnR GLS
#
# 用法:
#   ./experiments/run_gls_eval.sh                        # VerilogEval 全量
#   ./experiments/run_gls_eval.sh -l 10                  # 前 10 题快速测试
#   ./experiments/run_gls_eval.sh --dataset rtllm        # RTLLM 数据集
#   ./experiments/run_gls_eval.sh --synth-only           # 只跑综合后 GLS (不跑 P&R)
#   ./experiments/run_gls_eval.sh --baseline-only        # 只跑 baseline
#   ./experiments/run_gls_eval.sh --sparkle-only         # 只跑 Sparkle
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
DATASET="verilogeval"
SYNTH_ONLY=false
BASELINE_ONLY=false
SPARKLE_ONLY=false
EXTRA_ARGS=""
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# ── 解析参数 ──
while [[ $# -gt 0 ]]; do
    case "$1" in
        -m|--model)          MODEL="$2"; shift 2 ;;
        -w|--workers)        WORKERS="$2"; shift 2 ;;
        -l|--limit)          LIMIT="$2"; shift 2 ;;
        -r|--resume)         RESUME="--resume"; shift ;;
        -f|--filter)         FILTER="$2"; shift 2 ;;
        -d|--dataset)        DATASET="$2"; shift 2 ;;
        --synth-only)        SYNTH_ONLY=true; shift ;;
        --baseline-only)     BASELINE_ONLY=true; shift ;;
        --sparkle-only)      SPARKLE_ONLY=true; shift ;;
        *)                   EXTRA_ARGS="$EXTRA_ARGS $1"; shift ;;
    esac
done

# ── 环境准备 ──
if [[ -f "$PROJECT_ROOT/key.env" ]]; then
    set -a; source "$PROJECT_ROOT/key.env"; set +a
fi

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "错误: ANTHROPIC_API_KEY 未设置"
    exit 1
fi

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

# ── 检查 Docker ──
if ! docker info >/dev/null 2>&1; then
    echo "错误: GLS 需要 Docker (ORFS 容器内跑 iverilog)"
    exit 1
fi

echo "╭──────────────────────────────────────────────────────╮"
echo "│  Gate-Level Simulation (GLS) 功能验证评测              │"
echo "│  数据集: $DATASET"
echo "│  模型: $MODEL"
echo "│  并行度: $WORKERS workers"
echo "╰──────────────────────────────────────────────────────╯"
echo ""

# ── 构建公共参数 ──
LIMIT_ARG=""
[[ -n "$LIMIT" ]] && LIMIT_ARG="-l $LIMIT"
FILTER_ARG=""
[[ -n "$FILTER" ]] && FILTER_ARG="-f $FILTER"

PNR_ARGS=""
if ! $SYNTH_ONLY; then
    PNR_ARGS="--pnr --drc --lvs"
fi

mkdir -p results

START_TIME=$(date +%s)

# ============================================================
# Sparkle (我们的方法)
# ============================================================
if ! $BASELINE_ONLY; then
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Sparkle + GLS"
    echo "  开始: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    LOG_SPARKLE="results/gls_sparkle_${TIMESTAMP}.log"

    python3 agent/search.py \
        -m "$MODEL" -w "$WORKERS" \
        --dataset "$DATASET" \
        --synth --gls $PNR_ARGS \
        $LIMIT_ARG $FILTER_ARG $RESUME $EXTRA_ARGS \
        2>&1 | tee "$LOG_SPARKLE"

    echo ""
    echo "  Sparkle + GLS 完成"
fi

# ============================================================
# Baseline (直接 Verilog)
# ============================================================
if ! $SPARKLE_ONLY; then
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Baseline + GLS"
    echo "  开始: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    LOG_BASELINE="results/gls_baseline_${TIMESTAMP}.log"

    BASELINE_PNR=""
    if ! $SYNTH_ONLY; then
        BASELINE_PNR="--pnr"
    fi

    python3 experiments/baseline_verilog.py \
        -m "$MODEL" -w "$WORKERS" \
        --synth --gls $BASELINE_PNR \
        $LIMIT_ARG $FILTER_ARG $RESUME $EXTRA_ARGS \
        2>&1 | tee "$LOG_BASELINE"

    echo ""
    echo "  Baseline + GLS 完成"
fi

# ============================================================
# 汇总
# ============================================================
TOTAL_ELAPSED=$(( $(date +%s) - START_TIME ))
HOURS=$(( TOTAL_ELAPSED / 3600 ))
MINS=$(( (TOTAL_ELAPSED % 3600) / 60 ))
SECS=$(( TOTAL_ELAPSED % 60 ))

echo ""
echo "╭──────────────────────────────────────────────────────╮"
echo "│  GLS 功能验证评测完成!                                 │"
echo "│  总耗时: ${HOURS}h ${MINS}m ${SECS}s"
echo "│  结果目录: results/"
echo "╰──────────────────────────────────────────────────────╯"
echo ""
echo "查看结果:"
echo "  cat results/agent_run_*/summary.json"
echo "  cat results/baseline_verilog_*/summary.json"
