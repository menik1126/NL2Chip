#!/usr/bin/env bash
# ============================================================
# VerilogEval V2 (spec-to-rtl) 全流程评测
#
# 流程: NL spec → Sparkle Lean → SystemVerilog → Sim → Synth → P&R → DRC → LVS
# 数据集: verilog-eval/dataset_spec-to-rtl (156 题)
# 模型: claude-sonnet-4-5-20250929
#
# 用法:
#   ./experiments/run_spec2rtl_eval.sh                  # 全量 156 题 (编译+仿真+综合+P&R+DRC+LVS)
#   ./experiments/run_spec2rtl_eval.sh -l 10            # 前 10 题快速测试
#   ./experiments/run_spec2rtl_eval.sh -r               # 续跑 (跳过已通过)
#   ./experiments/run_spec2rtl_eval.sh -f "mux|fsm"     # 按正则过滤
#   ./experiments/run_spec2rtl_eval.sh --synth-only      # 只跑到综合 (不跑 P&R)
#   ./experiments/run_spec2rtl_eval.sh --no-pnr          # 同上
#   ./experiments/run_spec2rtl_eval.sh --arch-explore     # 额外跑架构探索
#   ./experiments/run_spec2rtl_eval.sh --ppa-opt          # 额外跑 PPA 优化
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
ARCH_EXPLORE=false
PPA_OPT=false
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
        --arch-explore)   ARCH_EXPLORE=true; shift ;;
        --ppa-opt)        PPA_OPT=true; shift ;;
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
echo "│  VerilogEval V2 (spec-to-rtl) 全流程评测              │"
echo "│  数据集: verilog-eval/dataset_spec-to-rtl (156 题)    │"
echo "│  模型: $MODEL"
echo "│  并行度: $WORKERS workers"
echo "╰──────────────────────────────────────────────────────╯"
echo ""

# 检查数据集
DATASET_DIR="$PROJECT_ROOT/verilog-eval/dataset_spec-to-rtl"
if [[ ! -d "$DATASET_DIR" ]]; then
    echo "错误: 数据集目录不存在: $DATASET_DIR"
    echo "  请先 clone verilog-eval: git clone https://github.com/NVlabs/verilog-eval.git"
    exit 1
fi

PROB_COUNT=$(ls "$DATASET_DIR"/*_prompt.txt 2>/dev/null | wc -l)
echo "  数据集: $PROB_COUNT 题 (spec-to-rtl)"

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
AGENT_ARGS="-m $MODEL -w $WORKERS"

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
# 第 1 步: 主实验 — 编译 + 仿真 + 综合 (+ P&R + DRC + LVS)
# ============================================================
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  第 1 步: 主实验 (编译 + 仿真 + 综合"
if $SYNTH_ONLY; then
    echo "           综合模式: 只跑到 Yosys 综合)"
    AGENT_ARGS="$AGENT_ARGS --synth"
else
    echo "           + P&R + DRC + LVS)"
    AGENT_ARGS="$AGENT_ARGS --pnr --drc --lvs"
fi
echo "  开始: $(date '+%Y-%m-%d %H:%M:%S')"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

LOG_MAIN="results/spec2rtl_main_${TIMESTAMP}.log"
START_TIME=$(date +%s)

python3 agent/search.py $AGENT_ARGS $EXTRA_ARGS 2>&1 | tee "$LOG_MAIN"

MAIN_ELAPSED=$(( $(date +%s) - START_TIME ))
echo ""
echo "  第 1 步完成 ($(( MAIN_ELAPSED / 60 ))m $(( MAIN_ELAPSED % 60 ))s)"

# ============================================================
# 第 2 步 (可选): 架构探索
# ============================================================
if $ARCH_EXPLORE; then
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  第 2 步: 架构探索 (复杂题 × 3 候选)"
    echo "  开始: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    # 63 道复杂题
    PPA_FILTER="Prob(030|035|037|045|054|063|066|067|068|071|074|075|076|078|079|080|082|084|085|086|095|096|097|100|107|108|109|110|111|112|114|115|118|119|120|121|122|123|124|125|127|128|129|133|134|137|138|139|140|141|142|143|144|146|148|149|150|151|152|153|154|155|156)"

    LOG_ARCH="results/spec2rtl_arch_${TIMESTAMP}.log"
    python3 agent/search.py \
        -m "$MODEL" -w "$WORKERS" \
        -f "$PPA_FILTER" \
        --arch-explore --arch-candidates 3 --arch-turns 40 \
        2>&1 | tee "$LOG_ARCH"

    echo "  架构探索完成"
fi

# ============================================================
# 第 3 步 (可选): PPA 优化
# ============================================================
if $PPA_OPT; then
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  第 3 步: PPA 优化 (复杂题 × 5 轮)"
    echo "  开始: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    PPA_FILTER="Prob(030|035|037|045|054|063|066|067|068|071|074|075|076|078|079|080|082|084|085|086|095|096|097|100|107|108|109|110|111|112|114|115|118|119|120|121|122|123|124|125|127|128|129|133|134|137|138|139|140|141|142|143|144|146|148|149|150|151|152|153|154|155|156)"

    LOG_PPA="results/spec2rtl_ppa_${TIMESTAMP}.log"
    python3 agent/search.py \
        -m "$MODEL" -w "$WORKERS" \
        -f "$PPA_FILTER" \
        --ppa-opt --ppa-iters 5 --ppa-turns 40 \
        2>&1 | tee "$LOG_PPA"

    echo "  PPA 优化完成"
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
echo "│  VerilogEval V2 (spec-to-rtl) 评测完成!               │"
echo "│  总耗时: ${HOURS}h ${MINS}m ${SECS}s"
echo "│  结果目录: results/ 下最新的 agent_run_* 目录"
echo "│  日志: $LOG_MAIN"
echo "╰──────────────────────────────────────────────────────╯"
echo ""
echo "查看结果:"
echo "  cat results/agent_run_*/summary.json"
echo "  python3 agent/report.py results/agent_run_*"
