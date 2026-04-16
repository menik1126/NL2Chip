#!/usr/bin/env bash
# ============================================================
# 实验 7: DRC + LVS 物理验证 (复用已有结果)
# 目的: 对已有 agent_run 结果补跑 P&R → DRC → LVS
# 不重新生成代码，直接复用 synth/ 目录
#
# 用法:
#   ./experiments/07_drc_lvs.sh                                    # 自动找最新 agent_run
#   ./experiments/07_drc_lvs.sh --run-dir results/agent_run_20260411_023642
#   ./experiments/07_drc_lvs.sh --run-dir results/agent_run_20260411_023642 --prob Prob005_notgate
#   ./experiments/07_drc_lvs.sh --drc-only                         # 只跑 DRC，跳过 LVS
#   ./experiments/07_drc_lvs.sh --lvs-only                         # 只跑 LVS，跳过 DRC
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# ── API key (Docker 不需要，但保持一致) ──
if [[ -f "$PROJECT_ROOT/key.env" ]]; then
    set -a; source "$PROJECT_ROOT/key.env"; set +a
fi

# ── 激活 Python 虚拟环境 ──
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

# ── Docker check ──
if ! docker info >/dev/null 2>&1; then
    echo "错误: DRC/LVS 需要 Docker，但 Docker 未运行"
    exit 1
fi

exec python3 experiments/drc_lvs_reuse.py "$@"
