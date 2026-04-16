#!/usr/bin/env bash
# ============================================================
# 一键跑全部实验
#
# 推荐执行顺序:
#   1. 先跑 01 (主实验) 和 05 (baseline) — 论文最核心的数据
#   2. 再跑 02 (P&R) — 收集 PPA 数据
#   3. 然后跑 03 (PPA 优化) 和 04 (架构探索) — 展示优化能力
#   4. 最后跑 06 (模型消融), 07 (DRC/LVS), 08 (等价验证)
#
# 用法:
#   ./experiments/run_all.sh              # 依次跑全部实验
#   ./experiments/run_all.sh --quick      # 每个实验只跑 10 题
#   ./experiments/run_all.sh --skip 01,05 # 跳过指定实验
#   ./experiments/run_all.sh --only 01,02 # 只跑指定实验
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# ── 激活 Python 虚拟环境 ──
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

# ── API key ──
if [[ -f "$PROJECT_ROOT/key.env" ]]; then
    set -a; source "$PROJECT_ROOT/key.env"; set +a
fi

# ── 参数解析 ──
QUICK=""
SKIP_LIST=""
ONLY_LIST=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --quick)  QUICK="true"; shift ;;
        --skip)   SKIP_LIST="$2"; shift 2 ;;
        --only)   ONLY_LIST="$2"; shift 2 ;;
        -h|--help)
            head -17 "$0" | tail -12
            exit 0
            ;;
        *) echo "未知选项: $1"; exit 1 ;;
    esac
done

if [[ -n "$QUICK" ]]; then
    echo "⚡ 快速模式: 每个实验只跑 10 题"
fi

# ── 依赖检查 ──
echo "╭──────────────────────────────────────────────╮"
echo "│  Sparkle 论文实验 — 一键运行                  │"
echo "╰──────────────────────────────────────────────╯"
echo ""

if ! command -v lake &>/dev/null; then
    echo "错误: 未找到 lake，请先安装 Lean 4 (elan)"
    exit 1
fi

if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
    echo "错误: ANTHROPIC_API_KEY 未设置"
    echo "  请在 key.env 中添加: ANTHROPIC_API_KEY=sk-ant-..."
    exit 1
fi

if ! python3 -c "import anthropic" &>/dev/null; then
    echo "错误: 未找到 anthropic 包，请运行: uv sync"
    exit 1
fi

HAS_DOCKER="false"
if docker info >/dev/null 2>&1; then
    HAS_DOCKER="true"
fi

HAS_IVERILOG="false"
if command -v iverilog &>/dev/null; then
    HAS_IVERILOG="true"
fi

echo "  lake:     $(command -v lake)"
echo "  Python:   $(which python3)"
echo "  iverilog: $(command -v iverilog 2>/dev/null || echo '未安装')"
echo "  Docker:   $([[ $HAS_DOCKER == "true" ]] && docker --version 2>/dev/null | head -1 || echo '未安装')"
echo ""

# ── 构建 Sparkle ──
echo "=== 构建 Sparkle ==="
lake build
echo ""

# ── 实验定义 ──
# 格式: "编号|脚本|描述|是否需要Docker"
EXPERIMENTS=(
    "01|01_main_benchmark.sh|主实验 — 全量 VerilogEval 156 题 (编译率+仿真通过率)|false"
    "05|05_baseline_verilog_direct.sh|Baseline — 直接生成 Verilog (对比实验)|false"
    "02|02_synthesis_pnr.sh|Synthesis + P&R (PPA 数据收集)|true"
    "03|03_ppa_optimization.sh|PPA 优化循环 — 63 道复杂题 x 5 轮迭代|true"
    "04|04_arch_exploration.sh|架构探索 — 63 道复杂题 x 3 候选架构|true"
    "06|06_model_ablation.sh|模型消融 (Sonnet 4.5 vs Sonnet 4 vs Haiku 4.5)|false"
    "07|07_drc_lvs.sh|DRC + LVS 物理验证|true"
    "08|08_equiv_check.sh|Yosys 形式化等价验证|false"
)

echo "实验列表:"
for entry in "${EXPERIMENTS[@]}"; do
    IFS='|' read -r num script desc needs_docker <<< "$entry"
    skip_mark=""
    if [[ -n "$ONLY_LIST" ]] && ! echo ",$ONLY_LIST," | grep -q ",$num,"; then
        skip_mark=" [跳过]"
    elif echo ",$SKIP_LIST," | grep -q ",$num,"; then
        skip_mark=" [跳过]"
    elif [[ "$needs_docker" == "true" && "$HAS_DOCKER" == "false" ]]; then
        skip_mark=" [跳过: 需要 Docker]"
    fi
    echo "  $num. $desc$skip_mark"
done
echo ""

# ── 运行实验 ──
mkdir -p results

START_TIME=$(date +%s)
PASSED=0
FAILED=0
SKIPPED=0

for entry in "${EXPERIMENTS[@]}"; do
    IFS='|' read -r num script desc needs_docker <<< "$entry"

    # 检查是否跳过
    if [[ -n "$ONLY_LIST" ]] && ! echo ",$ONLY_LIST," | grep -q ",$num,"; then
        SKIPPED=$((SKIPPED + 1))
        continue
    fi
    if echo ",$SKIP_LIST," | grep -q ",$num,"; then
        SKIPPED=$((SKIPPED + 1))
        continue
    fi
    if [[ "$needs_docker" == "true" && "$HAS_DOCKER" == "false" ]]; then
        echo "  跳过 $num ($desc): 需要 Docker"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  开始: $num. $desc"
    echo "  时间: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    if bash "$SCRIPT_DIR/$script"; then
        echo "  ✓ $num 成功"
        PASSED=$((PASSED + 1))
    else
        echo "  ✗ $num 失败 (继续下一个)"
        FAILED=$((FAILED + 1))
    fi
done

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))
HOURS=$((ELAPSED / 3600))
MINS=$(( (ELAPSED % 3600) / 60))

echo ""
echo "╭──────────────────────────────────────────────╮"
echo "│  全部实验完成!                                │"
echo "│  成功: $PASSED  失败: $FAILED  跳过: $SKIPPED              │"
echo "│  总耗时: ${HOURS}h ${MINS}m                   │"
echo "╰──────────────────────────────────────────────╯"
echo ""
echo "结果在 results/ 目录下"
