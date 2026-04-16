#!/usr/bin/env bash
# ============================================================
# PPA 优化 + 架构探索 — 只跑 63 道复杂题
#
# 用法:
#   ./experiments/run_ppa_arch.sh                # 先 PPA 优化再架构探索 (4 并行)
#   ./experiments/run_ppa_arch.sh --only-ppa     # 只跑 PPA 优化
#   ./experiments/run_ppa_arch.sh --only-arch    # 只跑架构探索
#   ./experiments/run_ppa_arch.sh -w 8           # 8 并行 (默认 4)
#   ./experiments/run_ppa_arch.sh --ppa-iters 3  # PPA 迭代轮数 (默认 5)
#   ./experiments/run_ppa_arch.sh --arch-candidates 5  # 候选架构数 (默认 3)
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# ── 激活虚拟环境 + API key ──
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi
if [[ -f "$PROJECT_ROOT/key.env" ]]; then
    set -a; source "$PROJECT_ROOT/key.env"; set +a
fi

# ── 参数 ──
MODEL="claude-sonnet-4-5-20250929"
PPA_ITERS=5
PPA_TURNS=40
ARCH_CANDIDATES=3
ARCH_TURNS=40
WORKERS=4
RUN_PPA="true"
RUN_ARCH="true"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --only-ppa)          RUN_ARCH=""; shift ;;
        --only-arch)         RUN_PPA=""; shift ;;
        -m|--model)          MODEL="$2"; shift 2 ;;
        --ppa-iters)         PPA_ITERS="$2"; shift 2 ;;
        --ppa-turns)         PPA_TURNS="$2"; shift 2 ;;
        --arch-candidates)   ARCH_CANDIDATES="$2"; shift 2 ;;
        --arch-turns)        ARCH_TURNS="$2"; shift 2 ;;
        -w|--workers)        WORKERS="$2"; shift 2 ;;
        -h|--help)           head -10 "$0" | tail -7; exit 0 ;;
        *) echo "未知选项: $1"; exit 1 ;;
    esac
done

# ── 63 道复杂题 ──
FILTER="Prob(030|035|037|045|054|063|066|067|068|071|074|075|076|078|079|080|082|084|085|086|095|096|097|100|107|108|109|110|111|112|114|115|118|119|120|121|122|123|124|125|127|128|129|133|134|137|138|139|140|141|142|143|144|146|148|149|150|151|152|153|154|155|156)"

# ── 依赖检查 ──
echo "╭──────────────────────────────────────────────╮"
echo "│  PPA 优化 + 架构探索 — 63 道复杂题            │"
echo "╰──────────────────────────────────────────────╯"
echo ""

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "错误: ANTHROPIC_API_KEY 未设置"; exit 1
fi
if ! command -v lake &>/dev/null; then
    echo "错误: 未找到 lake"; exit 1
fi
if ! docker info >/dev/null 2>&1; then
    echo "错误: PPA/架构探索需要 Docker (OpenROAD)，但 Docker 未运行"; exit 1
fi

echo "  模型:       $MODEL"
echo "  PPA 优化:   $([[ -n "$RUN_PPA" ]] && echo "${PPA_ITERS} 轮迭代" || echo "跳过")"
echo "  架构探索:   $([[ -n "$RUN_ARCH" ]] && echo "${ARCH_CANDIDATES} 候选" || echo "跳过")"
echo "  并行数:     $WORKERS"
echo "  题目数:     63"
echo ""

# ── 构建 ──
echo "=== 构建 Sparkle ==="
lake build
echo ""

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
mkdir -p results
START_TIME=$(date +%s)

# ── 03: PPA 优化循环 ──
if [[ -n "$RUN_PPA" ]]; then
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  PPA 优化循环 (${PPA_ITERS} 轮)"
    echo "  时间: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    LOG="results/ppa_opt_${TIMESTAMP}.log"
    ./agent/run_agent.sh \
        -m "$MODEL" \
        -w "$WORKERS" \
        -f "$FILTER" \
        --ppa-opt \
        --ppa-iters "$PPA_ITERS" \
        --ppa-turns "$PPA_TURNS" \
        > >(tee "$LOG") 2>&1 || echo "PPA 优化出错，继续..."

    echo ""
    echo "  PPA 优化完成，日志: $LOG"
fi

# ── 04: 架构探索 ──
if [[ -n "$RUN_ARCH" ]]; then
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  架构探索 (${ARCH_CANDIDATES} 候选)"
    echo "  时间: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    LOG="results/arch_explore_${TIMESTAMP}.log"
    ./agent/run_agent.sh \
        -m "$MODEL" \
        -w "$WORKERS" \
        -f "$FILTER" \
        --arch-explore \
        --arch-candidates "$ARCH_CANDIDATES" \
        --arch-turns "$ARCH_TURNS" \
        > >(tee "$LOG") 2>&1 || echo "架构探索出错，继续..."

    echo ""
    echo "  架构探索完成，日志: $LOG"
fi

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))
HOURS=$((ELAPSED / 3600))
MINS=$(( (ELAPSED % 3600) / 60))

echo ""
echo "╭──────────────────────────────────────────────╮"
echo "│  完成! 总耗时: ${HOURS}h ${MINS}m             │"
echo "│  结果: results/                              │"
echo "╰──────────────────────────────────────────────╯"
