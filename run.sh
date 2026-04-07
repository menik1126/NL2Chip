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
SKIP_BUILD=""
# 下游流程开关
RUN_LINT="true"
RUN_SIM="true"
RUN_SYNTH=""
RUN_PPA=""
RUN_SCHEMATIC=""
RUN_REPORT=""
SYNTH_PLATFORM="sky130hd"
CLOCK_NS="10.0"

SILICONCREW_DIR="$SCRIPT_DIR/siliconcrew"
WORKSPACE="$SILICONCREW_DIR/workspace"

usage() {
    cat <<'EOF'
用法: ./run.sh [选项]

完整管线: 自然语言 → Lean (Sparkle DSL) → SystemVerilog → 仿真 → 综合 → PPA

=== 第一阶段: NL → Lean → Verilog (autoformalize) ===
  -l, --limit N         只处理前 N 题 (默认: 全部 146 题)
  -r, --resume          跳过已通过的题目继续
  -m, --model MODEL     指定 LLM 模型 (默认: claude-sonnet-4-20250514)
  -f, --filter REGEX    按正则过滤题目 (如: "mux|counter")
  -d, --dry-run         只构建 prompt，不调用 LLM
  --skip-build          跳过 lake build (已构建过时)

=== 第二阶段: Verilog 下游流程 (siliconcrew) ===
  --no-lint             跳过 iverilog lint 检查
  --no-sim              跳过 iverilog 仿真验证
  --synth               启用 OpenROAD 逻辑综合 (需要 Docker)
  --ppa                 启用 PPA 提取 (面积/时序/功耗，需先 --synth)
  --schematic           启用原理图生成 (需要 yosys)
  --report              生成设计报告 (汇总所有结果)
  --all-downstream      启用全部下游流程 (lint+sim+synth+ppa+schematic+report)
  --platform PLATFORM   综合目标工艺 (默认: sky130hd)
  --clock-ns NS         目标时钟周期 (默认: 10.0)

=== 示例 ===
  ./run.sh -l 5                     # 跑前 5 题，含 lint + 仿真
  ./run.sh -l 3 --all-downstream    # 前 3 题，全流程到 PPA
  ./run.sh -l 1 --synth --ppa       # 1 题，加综合和 PPA
  ./run.sh -f "notgate" --schematic # 只跑 NOT 门，生成原理图
  ./run.sh --skip-build -r          # 跳过编译，续跑
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -l|--limit)       LIMIT="--limit $2"; shift 2 ;;
        -r|--resume)      RESUME="--resume"; shift ;;
        -m|--model)       MODEL="$2"; shift 2 ;;
        -f|--filter)      FILTER="--filter $2"; shift 2 ;;
        -d|--dry-run)     DRY_RUN="--dry-run"; shift ;;
        --skip-build)     SKIP_BUILD="true"; shift ;;
        --no-lint)        RUN_LINT=""; shift ;;
        --no-sim)         RUN_SIM=""; shift ;;
        --synth)          RUN_SYNTH="true"; shift ;;
        --ppa)            RUN_PPA="true"; shift ;;
        --schematic)      RUN_SCHEMATIC="true"; shift ;;
        --report)         RUN_REPORT="true"; shift ;;
        --all-downstream) RUN_SYNTH="true"; RUN_PPA="true"; RUN_SCHEMATIC="true"; RUN_REPORT="true"; shift ;;
        --platform)       SYNTH_PLATFORM="$2"; shift 2 ;;
        --clock-ns)       CLOCK_NS="$2"; shift 2 ;;
        -h|--help)        usage ;;
        *) echo "未知选项: $1"; usage ;;
    esac
done

# ─────────────────────────────────────────────────────────────
# 依赖检查
# ─────────────────────────────────────────────────────────────
echo "=== 检查依赖 ==="

if ! command -v lake &>/dev/null; then
    echo "错误: 未找到 lake，请先安装 Lean 4 (elan)"
    echo "  curl https://elan-init.trycloudflare.com/ -sSf | sh"
    exit 1
fi

if ! python3 -c "import anthropic" &>/dev/null; then
    echo "未找到 anthropic 包，正在安装..."
    pip install anthropic
fi

if [ ! -f key.env ]; then
    echo "错误: 未找到 key.env，请创建并填入 API Key:"
    echo '  echo '\''ANTHROPIC_API_KEY=sk-ant-...'\'' > key.env'
    exit 1
fi

if [ -n "$RUN_LINT" ] || [ -n "$RUN_SIM" ]; then
    if ! command -v iverilog &>/dev/null; then
        echo "警告: 未找到 iverilog，lint/仿真将被跳过"
        echo "  安装: sudo apt install iverilog"
        RUN_LINT=""
        RUN_SIM=""
    fi
fi

if [ -n "$RUN_SYNTH" ]; then
    if ! command -v docker &>/dev/null; then
        echo "警告: 未找到 docker，综合将被跳过"
        echo "  OpenROAD 综合需要 Docker 环境"
        RUN_SYNTH=""
        RUN_PPA=""
    fi
fi

if [ -n "$RUN_SCHEMATIC" ]; then
    if ! command -v yosys &>/dev/null; then
        echo "警告: 未找到 yosys，原理图生成将被跳过"
        echo "  安装: sudo apt install yosys"
        RUN_SCHEMATIC=""
    fi
fi

echo "  lake: $(command -v lake)"
echo "  iverilog: $(command -v iverilog 2>/dev/null || echo '未安装')"
echo "  docker: $(command -v docker 2>/dev/null || echo '未安装')"
echo "  yosys: $(command -v yosys 2>/dev/null || echo '未安装')"
echo ""

# ─────────────────────────────────────────────────────────────
# 第一阶段: NL → Lean → Verilog
# ─────────────────────────────────────────────────────────────
if [ -z "$SKIP_BUILD" ]; then
    echo "=== [阶段 1/2] 构建 Sparkle ==="
    lake build
    echo ""
fi

echo "=== [阶段 1/2] NL → Lean → Verilog (autoformalize) ==="
echo "模型: $MODEL"
echo ""

python3 autoformalize.py \
    --model "$MODEL" \
    $LIMIT $RESUME $FILTER $DRY_RUN

# ─────────────────────────────────────────────────────────────
# 收集生成的 Verilog 文件
# ─────────────────────────────────────────────────────────────
# autoformalize.py 在 lake build 的 stdout 中输出生成的 SV
# 同时仿真结果保存在 results/ 下
# 我们从 Generated/ 目录重新构建，提取 #synthesizeVerilog 输出

echo ""
echo "=== [阶段 2/2] Verilog 下游流程 ==="

# 确保 workspace 存在
mkdir -p "$WORKSPACE"

# 找到最新的 results 目录中的仿真文件
LATEST_RUN=$(ls -td results/run_* 2>/dev/null | head -1)

if [ -z "$LATEST_RUN" ]; then
    echo "未找到 autoformalize 结果目录，跳过下游流程"
    exit 0
fi

echo "使用结果目录: $LATEST_RUN"

# 收集已通过编译的 .lean 文件，重新 build 提取 Verilog
SV_COUNT=0
LINT_PASS=0
LINT_FAIL=0
SIM_PASS=0
SIM_FAIL=0
SYNTH_PASS=0
SYNTH_FAIL=0

for lean_file in Generated/*.lean; do
    [ -f "$lean_file" ] || continue
    prob_id=$(basename "$lean_file" .lean)

    # 用 lake build 提取生成的 Verilog
    echo ""
    echo "── $prob_id ──"
    BUILD_OUTPUT=$(lake build "Generated.$prob_id" 2>&1 || true)

    # 提取 SystemVerilog (在 info 输出中)
    SV_CODE=$(echo "$BUILD_OUTPUT" | sed -n '/\/\/ Generated by Sparkle HDL/,/endmodule/p')

    if [ -z "$SV_CODE" ]; then
        echo "  跳过: 无法提取 Verilog"
        continue
    fi

    SV_FILE="$WORKSPACE/${prob_id}.sv"
    echo "$SV_CODE" > "$SV_FILE"
    SV_COUNT=$((SV_COUNT + 1))
    echo "  Verilog → $SV_FILE"

    # 提取模块名
    MOD_NAME=$(echo "$SV_CODE" | grep -oP 'module\s+\K\w+' | head -1)

    # ── Lint ──
    if [ -n "$RUN_LINT" ] && [ -n "$MOD_NAME" ]; then
        LINT_OUT=$(iverilog -t null -g2012 "$SV_FILE" 2>&1 || true)
        if [ -z "$LINT_OUT" ]; then
            echo "  Lint: PASS"
            LINT_PASS=$((LINT_PASS + 1))
        else
            echo "  Lint: FAIL"
            echo "    $LINT_OUT" | head -3
            LINT_FAIL=$((LINT_FAIL + 1))
        fi
    fi

    # ── 仿真 (使用 VerilogEval 测试向量) ──
    if [ -n "$RUN_SIM" ] && [ -n "$MOD_NAME" ]; then
        REF_SV="verilog-eval/dataset_spec-to-rtl/${prob_id}_ref.sv"
        TEST_SV="verilog-eval/dataset_spec-to-rtl/${prob_id}_test.sv"

        if [ -f "$REF_SV" ] && [ -f "$TEST_SV" ]; then
            SIM_DIR="$WORKSPACE/sim_${prob_id}"
            mkdir -p "$SIM_DIR"

            # 编译+仿真
            if iverilog -g2012 -o "$SIM_DIR/sim.vvp" "$REF_SV" "$SV_FILE" "$TEST_SV" 2>/dev/null; then
                SIM_OUT=$(timeout 30 vvp "$SIM_DIR/sim.vvp" 2>&1 || true)
                if echo "$SIM_OUT" | grep -q "Mismatches: 0"; then
                    echo "  Sim:  PASS"
                    SIM_PASS=$((SIM_PASS + 1))
                else
                    MISMATCH=$(echo "$SIM_OUT" | grep -oP 'Mismatches:\s*\K\d+' || echo "?")
                    echo "  Sim:  FAIL (${MISMATCH} mismatches)"
                    SIM_FAIL=$((SIM_FAIL + 1))
                fi
            else
                echo "  Sim:  编译失败"
                SIM_FAIL=$((SIM_FAIL + 1))
            fi
        fi
    fi

    # ── 综合 (OpenROAD via siliconcrew) ──
    if [ -n "$RUN_SYNTH" ] && [ -n "$MOD_NAME" ]; then
        echo "  Synth: 启动 OpenROAD ($SYNTH_PLATFORM, clk=${CLOCK_NS}ns)..."
        SYNTH_OUT=$(cd "$SILICONCREW_DIR" && \
            RTL_WORKSPACE="$WORKSPACE" python3 -c "
import sys, json
sys.path.insert(0, '.')
from src.tools.synthesis_manager import start_synthesis_job, get_synthesis_job_status
import time

result = start_synthesis_job(
    workspace='$WORKSPACE',
    verilog_files=['$SV_FILE'],
    top_module='$MOD_NAME',
    platform='$SYNTH_PLATFORM',
    clock_period_ns=$CLOCK_NS,
)
if 'job_id' not in result:
    print(json.dumps(result))
    sys.exit(1)

job_id = result['job_id']
for _ in range(150):
    status = get_synthesis_job_status(job_id, workspace='$WORKSPACE')
    if status.get('status') in ('completed', 'failed'):
        print(json.dumps(status, indent=2))
        sys.exit(0 if status['status'] == 'completed' else 1)
    time.sleep(2)
print('TIMEOUT')
sys.exit(1)
" 2>&1 || true)

        if echo "$SYNTH_OUT" | grep -q '"status": "completed"'; then
            echo "  Synth: PASS"
            SYNTH_PASS=$((SYNTH_PASS + 1))

            # ── PPA 提取 ──
            if [ -n "$RUN_PPA" ]; then
                PPA_OUT=$(cd "$SILICONCREW_DIR" && \
                    RTL_WORKSPACE="$WORKSPACE" python3 -c "
import sys, json
sys.path.insert(0, '.')
from src.tools.get_ppa import get_ppa_metrics
metrics = get_ppa_metrics('$WORKSPACE/orfs_logs')
print(json.dumps(metrics, indent=2))
" 2>&1 || true)
                echo "  PPA:  $PPA_OUT" | head -10
            fi
        else
            echo "  Synth: FAIL"
            SYNTH_FAIL=$((SYNTH_FAIL + 1))
        fi
    fi

    # ── 原理图 ──
    if [ -n "$RUN_SCHEMATIC" ] && [ -n "$MOD_NAME" ]; then
        SVG_OUT=$(cd "$SILICONCREW_DIR" && \
            RTL_WORKSPACE="$WORKSPACE" python3 -c "
import sys
sys.path.insert(0, '.')
from src.tools.generate_schematic import generate_schematic
result = generate_schematic('$SV_FILE', '$MOD_NAME', cwd='$WORKSPACE')
print(result.get('svg_path', result.get('error', 'unknown error')))
" 2>&1 || true)
        echo "  Schematic: $SVG_OUT"
    fi

done

# ─────────────────────────────────────────────────────────────
# 设计报告
# ─────────────────────────────────────────────────────────────
if [ -n "$RUN_REPORT" ] && [ $SV_COUNT -gt 0 ]; then
    echo ""
    echo "── 生成设计报告 ──"
    cd "$SILICONCREW_DIR" && \
        RTL_WORKSPACE="$WORKSPACE" python3 -c "
import sys
sys.path.insert(0, '.')
from src.tools.design_report import save_design_report
path = save_design_report('$WORKSPACE')
print(f'报告已生成: {path}')
" 2>&1 || echo "报告生成失败"
fi

# ─────────────────────────────────────────────────────────────
# 汇总
# ─────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "  完整管线汇总"
echo "============================================================"
echo "  Verilog 文件: $SV_COUNT"
[ -n "$RUN_LINT" ]      && echo "  Lint:         $LINT_PASS pass / $LINT_FAIL fail"
[ -n "$RUN_SIM" ]       && echo "  仿真:         $SIM_PASS pass / $SIM_FAIL fail"
[ -n "$RUN_SYNTH" ]     && echo "  综合:         $SYNTH_PASS pass / $SYNTH_FAIL fail"
echo "  结果目录:     $LATEST_RUN"
echo "  Workspace:    $WORKSPACE"
echo "============================================================"
