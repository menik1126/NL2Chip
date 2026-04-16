#!/usr/bin/env bash
# ============================================================
# 重跑全部受 async reset bug 影响的实验
# Bug: Verilog 后端硬编码异步复位，现已修复为同步复位
# 并行度: 8 workers
#
# 断点续跑:
#   ./rerun_all_experiments.sh              # 从头跑全部
#   ./rerun_all_experiments.sh --from 2     # 从第 2 步开始
#   ./rerun_all_experiments.sh --from 3     # 从第 3 步开始
#   ./rerun_all_experiments.sh --only 2     # 只跑第 2 步
#   ./rerun_all_experiments.sh --only 1,3,5 # 只跑第 1、3、5 步
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

WORKERS=8
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
MASTER_LOG="results/rerun_all_${TIMESTAMP}.log"
CHECKPOINT_FILE="results/.rerun_checkpoint"

# ── 解析参数 ──
START_FROM=1
ONLY_STEPS=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --from)  START_FROM="$2"; shift 2 ;;
        --only)  ONLY_STEPS="$2"; shift 2 ;;
        -w|--workers) WORKERS="$2"; shift 2 ;;
        *)       echo "未知参数: $1"; exit 1 ;;
    esac
done

# 判断某一步是否需要运行
should_run() {
    local step=$1
    if [[ -n "$ONLY_STEPS" ]]; then
        # --only 模式: 只跑指定的步骤
        echo ",$ONLY_STEPS," | grep -q ",$step,"
        return $?
    fi
    # --from 模式: 跑 >= START_FROM 的步骤
    [[ $step -ge $START_FROM ]]
}

# ── 环境准备 ──
if [[ -f "$SCRIPT_DIR/key.env" ]]; then
    set -a; source "$SCRIPT_DIR/key.env"; set +a
fi

if [ -f "$SCRIPT_DIR/.venv/bin/activate" ]; then
    source "$SCRIPT_DIR/.venv/bin/activate"
fi

mkdir -p results

echo "╭──────────────────────────────────────────────╮" | tee "$MASTER_LOG"
echo "│  Sparkle 实验全量重跑 (async reset bug fix)   │" | tee -a "$MASTER_LOG"
echo "│  并行度: ${WORKERS} workers                       │" | tee -a "$MASTER_LOG"
echo "│  时间: $(date '+%Y-%m-%d %H:%M:%S')              │" | tee -a "$MASTER_LOG"
if [[ -n "$ONLY_STEPS" ]]; then
    echo "│  模式: 只跑步骤 ${ONLY_STEPS}                       │" | tee -a "$MASTER_LOG"
elif [[ $START_FROM -gt 1 ]]; then
    echo "│  模式: 从第 ${START_FROM} 步续跑                       │" | tee -a "$MASTER_LOG"
fi
echo "╰──────────────────────────────────────────────╯" | tee -a "$MASTER_LOG"
echo "" | tee -a "$MASTER_LOG"

# ── 先 rebuild Sparkle (确保用修复后的代码) ──
echo "=== [0/6] 构建 Sparkle ===" | tee -a "$MASTER_LOG"
lake build 2>&1 | tee -a "$MASTER_LOG"
echo "" | tee -a "$MASTER_LOG"

START_TIME=$(date +%s)
PASS=0
FAIL=0
SKIP=0

run_step() {
    local step_num="$1"
    local step_idx="${step_num%%/*}"  # "2/6" -> "2"
    local step_name="$2"
    local log_file="$3"
    shift 3
    local cmd=("$@")

    # 检查是否需要运行
    if ! should_run "$step_idx"; then
        echo "  ⏭ [${step_num}] ${step_name} (跳过)" | tee -a "$MASTER_LOG"
        SKIP=$((SKIP + 1))
        return 0
    fi

    echo "" | tee -a "$MASTER_LOG"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" | tee -a "$MASTER_LOG"
    echo "  [${step_num}] ${step_name}" | tee -a "$MASTER_LOG"
    echo "  开始: $(date '+%Y-%m-%d %H:%M:%S')" | tee -a "$MASTER_LOG"
    echo "  日志: ${log_file}" | tee -a "$MASTER_LOG"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" | tee -a "$MASTER_LOG"

    local step_start=$SECONDS
    if "${cmd[@]}" > >(tee "$log_file") 2>&1; then
        local elapsed=$(( SECONDS - step_start ))
        echo "  ✓ [${step_num}] ${step_name} 完成 (${elapsed}s)" | tee -a "$MASTER_LOG"
        # 记录 checkpoint
        echo "$step_idx" > "$CHECKPOINT_FILE"
        PASS=$((PASS + 1))
    else
        local elapsed=$(( SECONDS - step_start ))
        echo "  ✗ [${step_num}] ${step_name} 失败 (${elapsed}s)，继续下一步..." | tee -a "$MASTER_LOG"
        FAIL=$((FAIL + 1))
    fi
}

# ── PPA/Arch 的题目过滤 (63 道复杂题) ──
PPA_FILTER="Prob(030|035|037|045|054|063|066|067|068|071|074|075|076|078|079|080|082|084|085|086|095|096|097|100|107|108|109|110|111|112|114|115|118|119|120|121|122|123|124|125|127|128|129|133|134|137|138|139|140|141|142|143|144|146|148|149|150|151|152|153|154|155|156)"

# ============================================================
# 实验 1/6: 主实验 — 全量 156 题 + 综合
# ============================================================
run_step "1/6" "主实验: 全量 156 题 (编译+仿真+综合)" \
    "results/exp01_main_${TIMESTAMP}.log" \
    ./agent/run_agent.sh -m claude-sonnet-4-5-20250929 -w "$WORKERS" --synth --no-repl

# ============================================================
# 实验 2/6: P&R — 全量 156 题 + 综合 + Place&Route + 多角 STA
# ============================================================
run_step "2/6" "P&R: 全量 156 题 (综合+P&R+多角STA)" \
    "results/exp02_pnr_${TIMESTAMP}.log" \
    ./agent/run_agent.sh -m claude-sonnet-4-5-20250929 -w "$WORKERS" --pnr --corners --no-repl

# ============================================================
# 实验 3/6: PPA 优化 — 63 道复杂题 × 5 轮迭代
# ============================================================
run_step "3/6" "PPA 优化: 63 题 × 5 轮" \
    "results/exp03_ppa_opt_${TIMESTAMP}.log" \
    ./agent/run_agent.sh -m claude-sonnet-4-5-20250929 -w "$WORKERS" \
        -f "$PPA_FILTER" --ppa-opt --ppa-iters 5 --ppa-turns 40 --no-repl

# ============================================================
# 实验 4/6: 架构探索 — 63 道复杂题 × 3 候选架构
# ============================================================
run_step "4/6" "架构探索: 63 题 × 3 候选" \
    "results/exp04_arch_explore_${TIMESTAMP}.log" \
    ./agent/run_agent.sh -m claude-sonnet-4-5-20250929 -w "$WORKERS" \
        -f "$PPA_FILTER" --arch-explore --arch-candidates 3 --arch-turns 40 --no-repl

# ============================================================
# 实验 5/6: DRC + LVS (复用实验 2 的 P&R 结果)
# ============================================================
if should_run 5; then
    # 找到最新的带 P&R 结果的 agent_run
    LATEST_PNR_RUN=$(ls -dt results/agent_run_* 2>/dev/null | head -1)
    if [[ -n "$LATEST_PNR_RUN" ]]; then
        run_step "5/6" "DRC + LVS 物理验证" \
            "results/exp07_drc_lvs_${TIMESTAMP}.log" \
            ./experiments/07_drc_lvs.sh --run-dir "$LATEST_PNR_RUN"
    else
        echo "  ⚠ [5/6] 跳过 DRC/LVS: 未找到 P&R 结果目录" | tee -a "$MASTER_LOG"
    fi
fi

# ============================================================
# 实验 6/6: 模型消融 — Sonnet 4.5 / Sonnet 4 / Haiku 4.5
# ============================================================
run_step "6/6" "模型消融: 3 模型 × 156 题" \
    "results/exp06_ablation_${TIMESTAMP}.log" \
    ./experiments/06_model_ablation.sh -w "$WORKERS"

# ============================================================
# 清理 checkpoint
# ============================================================
rm -f "$CHECKPOINT_FILE"

# ============================================================
# 汇总
# ============================================================
END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))
HOURS=$((ELAPSED / 3600))
MINS=$(( (ELAPSED % 3600) / 60 ))

echo "" | tee -a "$MASTER_LOG"
echo "╭──────────────────────────────────────────────╮" | tee -a "$MASTER_LOG"
echo "│  全部实验重跑完成!                             │" | tee -a "$MASTER_LOG"
echo "│  成功: $PASS  失败: $FAIL  跳过: $SKIP              │" | tee -a "$MASTER_LOG"
echo "│  总耗时: ${HOURS}h ${MINS}m                         │" | tee -a "$MASTER_LOG"
echo "╰──────────────────────────────────────────────╯" | tee -a "$MASTER_LOG"
echo "" | tee -a "$MASTER_LOG"
echo "主日志: $MASTER_LOG" | tee -a "$MASTER_LOG"
echo "结果目录: results/" | tee -a "$MASTER_LOG"
