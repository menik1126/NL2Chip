#!/usr/bin/env bash
# ============================================================
# 实验 6: 模型消融实验
# 目的: 对比不同模型在 Sparkle HDL 生成上的表现
# 模型: Sonnet 4.5 vs Sonnet 4 vs Haiku 4.5
#
# 用法:
#   ./experiments/06_model_ablation.sh              # 全量 156 题
#   ./experiments/06_model_ablation.sh -l 20        # 前 20 题 (快速验证)
#   ./experiments/06_model_ablation.sh -l 20 -w 4   # 4 并发
#   ./experiments/06_model_ablation.sh --synth       # 含综合 PPA
#   ./experiments/06_model_ablation.sh --skip sonnet-4-5  # 跳过某模型
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
ABLATION_DIR="results/exp06_ablation_${TIMESTAMP}"
mkdir -p "$ABLATION_DIR"

# ── 默认参数 ──
LIMIT=""
WORKERS=""
EXTRA_ARGS=()
SKIP_MODELS=()

# ── 解析参数 ──
while [[ $# -gt 0 ]]; do
    case "$1" in
        -l|--limit)   LIMIT="$2"; shift 2 ;;
        -w|--workers) WORKERS="$2"; shift 2 ;;
        --skip)       SKIP_MODELS+=("$2"); shift 2 ;;
        *)            EXTRA_ARGS+=("$1"); shift ;;
    esac
done

# ── 模型列表 ──
declare -A MODEL_IDS=(
    ["sonnet-4-5"]="claude-sonnet-4-5-20250929"
    ["sonnet-4"]="claude-sonnet-4-6"
    ["haiku-4-5"]="claude-haiku-4-5-20251001"
)
MODEL_ORDER=("sonnet-4-5" "sonnet-4" "haiku-4-5")

# ── Banner ──
echo "╭──────────────────────────────────────────────╮"
echo "│  实验 6: 模型消融实验                         │"
echo "│  对比: Sonnet 4.5 / Sonnet 4 / Haiku 4.5     │"
echo "│  结果: $ABLATION_DIR"
echo "╰──────────────────────────────────────────────╯"
echo ""

if [[ -n "$LIMIT" ]]; then
    echo "  题数限制: $LIMIT"
fi
if [[ -n "$WORKERS" ]]; then
    echo "  并发数:   $WORKERS"
fi
if [[ ${#SKIP_MODELS[@]} -gt 0 ]]; then
    echo "  跳过模型: ${SKIP_MODELS[*]}"
fi
if [[ ${#EXTRA_ARGS[@]} -gt 0 ]]; then
    echo "  额外参数: ${EXTRA_ARGS[*]}"
fi
echo ""

# ── 记录实验配置 ──
cat > "$ABLATION_DIR/config.json" <<EOF
{
    "experiment": "06_model_ablation",
    "timestamp": "$TIMESTAMP",
    "limit": ${LIMIT:-null},
    "workers": ${WORKERS:-null},
    "extra_args": "$(IFS=' '; echo "${EXTRA_ARGS[*]+"${EXTRA_ARGS[*]}"}")",
    "skip_models": [$(printf '"%s",' "${SKIP_MODELS[@]+"${SKIP_MODELS[@]}"}" | sed 's/,$//')],
    "models": {
        "sonnet-4-5": "${MODEL_IDS[sonnet-4-5]}",
        "sonnet-4": "${MODEL_IDS[sonnet-4]}",
        "haiku-4-5": "${MODEL_IDS[haiku-4-5]}"
    }
}
EOF

# ── 构建公共参数 ──
build_agent_args() {
    local model_id="$1"
    local args=("-m" "$model_id")
    [[ -n "$LIMIT" ]] && args+=("-l" "$LIMIT")
    [[ -n "$WORKERS" ]] && args+=("-w" "$WORKERS")
    args+=("${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}")
    echo "${args[@]}"
}

# ── 逐模型运行 ──
PASS=0
FAIL=0
declare -A RUN_DIRS=()

for model_key in "${MODEL_ORDER[@]}"; do
    # 检查是否跳过
    skip=false
    for s in "${SKIP_MODELS[@]+"${SKIP_MODELS[@]}"}"; do
        if [[ "$s" == "$model_key" ]]; then
            skip=true
            break
        fi
    done
    if $skip; then
        echo "⏭  跳过: $model_key"
        echo ""
        continue
    fi

    model_id="${MODEL_IDS[$model_key]}"
    LOG="$ABLATION_DIR/${model_key}.log"

    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  模型: $model_key ($model_id)"
    echo "  日志: $LOG"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    START_SEC=$SECONDS

    AGENT_ARGS=$(build_agent_args "$model_id")
    if ./agent/run_agent.sh $AGENT_ARGS > >(tee "$LOG") 2>&1; then
        ELAPSED=$(( SECONDS - START_SEC ))
        echo ""
        echo "  ✓ $model_key 完成 (${ELAPSED}s)"
        PASS=$((PASS + 1))
    else
        ELAPSED=$(( SECONDS - START_SEC ))
        echo ""
        echo "  ✗ $model_key 失败 (${ELAPSED}s)，继续下一个模型..."
        FAIL=$((FAIL + 1))
    fi

    # 找到本次 run 的结果目录 (最新的 agent_run_*)
    LATEST_RUN=$(ls -dt results/agent_run_* 2>/dev/null | head -1)
    if [[ -n "$LATEST_RUN" ]]; then
        RUN_DIRS[$model_key]="$LATEST_RUN"
        # 软链接到消融目录方便对比
        ln -sfn "../../$LATEST_RUN" "$ABLATION_DIR/${model_key}_run"
    fi

    echo ""
done

# ── 汇总对比 ──
echo ""
echo "╭──────────────────────────────────────────────╮"
echo "│  模型消融结果汇总                             │"
echo "╰──────────────────────────────────────────────╯"
echo ""
printf "%-14s  %-35s  %7s  %10s  %10s  %8s\n" \
    "Model" "ID" "Total" "Compile" "Sim Pass" "Time(s)"
printf "%-14s  %-35s  %7s  %10s  %10s  %8s\n" \
    "--------------" "-----------------------------------" "-------" "----------" "----------" "--------"

# 收集各模型 summary.json 数据
declare -A SUMMARIES=()
for model_key in "${MODEL_ORDER[@]}"; do
    run_dir="${RUN_DIRS[$model_key]:-}"
    if [[ -z "$run_dir" ]]; then
        printf "%-14s  %-35s  %7s  %10s  %10s  %8s\n" \
            "$model_key" "(skipped)" "-" "-" "-" "-"
        continue
    fi

    summary_file="$run_dir/summary.json"
    if [[ -f "$summary_file" ]]; then
        total=$(python3 -c "import json; d=json.load(open('$summary_file')); print(d.get('total', '?'))")
        compile_rate=$(python3 -c "import json; d=json.load(open('$summary_file')); print(d.get('compile_rate', '?'))")
        sim_rate=$(python3 -c "import json; d=json.load(open('$summary_file')); print(d.get('sim_rate', '?'))")
        elapsed=$(python3 -c "import json; d=json.load(open('$summary_file')); print(d.get('elapsed_seconds', '?'))")

        printf "%-14s  %-35s  %7s  %10s  %10s  %8s\n" \
            "$model_key" "${MODEL_IDS[$model_key]}" "$total" "$compile_rate" "$sim_rate" "$elapsed"

        SUMMARIES[$model_key]="$summary_file"
    else
        printf "%-14s  %-35s  %7s  %10s  %10s  %8s\n" \
            "$model_key" "${MODEL_IDS[$model_key]}" "?" "?" "?" "?"
    fi
done

echo ""

# ── 生成对比 JSON ──
python3 -c "
import json, sys
from pathlib import Path

ablation_dir = Path('$ABLATION_DIR')
results = {}

model_map = {
    'sonnet-4-5': '${MODEL_IDS[sonnet-4-5]}',
    'sonnet-4':   '${MODEL_IDS[sonnet-4]}',
    'haiku-4-5':  '${MODEL_IDS[haiku-4-5]}',
}

for key, model_id in model_map.items():
    link = ablation_dir / f'{key}_run'
    if not link.exists():
        continue
    summary = link / 'summary.json'
    if summary.exists():
        with open(summary) as f:
            data = json.load(f)
        results[key] = {
            'model_id': model_id,
            'total': data.get('total'),
            'compile_pass': data.get('compile_pass'),
            'compile_rate': data.get('compile_rate'),
            'sim_pass': data.get('sim_pass'),
            'sim_rate': data.get('sim_rate'),
            'synth_pass': data.get('synth_pass'),
            'elapsed_seconds': data.get('elapsed_seconds'),
            'run_dir': str(link.resolve()),
        }

comparison = {
    'experiment': '06_model_ablation',
    'timestamp': '$TIMESTAMP',
    'models': results,
}

out = ablation_dir / 'comparison.json'
with open(out, 'w') as f:
    json.dump(comparison, f, indent=2, ensure_ascii=False)
print(f'对比结果已写入: {out}')
" 2>&1

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  实验 6 完成  (成功: $PASS, 失败: $FAIL)"
echo "  结果目录: $ABLATION_DIR"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
