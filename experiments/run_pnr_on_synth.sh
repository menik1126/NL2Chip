#!/usr/bin/env bash
# ============================================================
# 对已有 Sparkle (synth) 结果补跑 P&R
# 输入: agent_run 目录 (已有 synth 结果, pnr=0)
# 输出: 在同一目录下补充 P&R 结果
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# 激活 Python 虚拟环境
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

# 默认使用 Sparkle (synth) 的结果目录
RUN_DIR="${1:-results/agent_run_20260413_025552}"

if [ ! -d "$RUN_DIR" ]; then
    echo "错误: 目录不存在: $RUN_DIR"
    exit 1
fi

echo "╭──────────────────────────────────────────────╮"
echo "│  补跑 P&R (对已综合通过的设计)                │"
echo "│  结果目录: $RUN_DIR"
echo "╰──────────────────────────────────────────────╯"

# 检查 Docker
if ! docker info >/dev/null 2>&1; then
    echo "错误: P&R 需要 Docker，但 Docker 未运行"
    exit 1
fi

python3 - "$RUN_DIR" <<'PYEOF'
import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent if "__file__" in dir() else Path.cwd()
sys.path.insert(0, str(PROJECT_ROOT / "agent"))

from evaluator import Evaluator

run_dir = Path(sys.argv[1]).resolve()
results_file = run_dir / "results.jsonl"
sv_dir = run_dir / "sv"

if not results_file.exists():
    print(f"错误: {results_file} 不存在")
    sys.exit(1)

# 清理之前失败的 pnr 记录
lines = results_file.read_text().splitlines()
clean_lines = [l for l in lines if '"phase": "pnr"' not in l]
if len(clean_lines) < len(lines):
    print(f"清理 {len(lines) - len(clean_lines)} 条旧 P&R 记录")
    results_file.write_text("\n".join(clean_lines) + "\n")

# 找出所有综合通过的题目
synth_passed = []
results_data = {}
for line in results_file.read_text().splitlines():
    try:
        r = json.loads(line)
        pid = r.get("prob_id", "")
        results_data[pid] = r
        if r.get("synth_pass"):
            synth_passed.append(pid)
    except Exception:
        pass

# 去重
synth_passed = sorted(set(synth_passed))
print(f"\n综合通过: {len(synth_passed)} 个设计, 开始跑 P&R...\n")

evaluator = Evaluator(
    project_root=PROJECT_ROOT,
    enable_synth=True,
    enable_pnr=True,
)

pnr_pass = 0
pnr_fail = 0
pnr_results = []

for i, prob_id in enumerate(synth_passed, 1):
    sv_path = sv_dir / f"{prob_id}.sv"
    if not sv_path.exists():
        print(f"  [{i}/{len(synth_passed)}] {prob_id}  跳过 (SV 文件不存在)")
        continue

    sv_code = sv_path.read_text()

    # 从 SV 中提取 top module 名
    m = re.search(r"module\s+(\w+)\s*\(", sv_code)
    top_module = m.group(1) if m else "TopModule"

    print(f"  [{i}/{len(synth_passed)}] {prob_id}  ", end="", flush=True)

    pr = evaluator._run_pnr(prob_id, sv_code, top_module, run_dir)

    if pr.get("pnr_pass"):
        pnr_pass += 1
        wns = pr.get("wns_ns")
        pwr = pr.get("power_uw")
        print(f"P✓" +
              (f" WNS={wns:.2f}" if wns is not None else "") +
              (f" Pwr={pwr:.2f}μW" if pwr is not None else ""))
    else:
        pnr_fail += 1
        err = pr.get("pnr_error", "unknown")[:60]
        print(f"P✗  {err}")

    pnr_results.append({"prob_id": prob_id, "phase": "pnr", **pr})

# 追加 P&R 结果到 results.jsonl
with open(results_file, "a") as f:
    for r in pnr_results:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

# 更新 summary.json
summary_file = run_dir / "summary.json"
if summary_file.exists():
    summary = json.loads(summary_file.read_text())
    summary["pnr_pass"] = pnr_pass
    summary["pnr_enabled"] = True
    summary_file.write_text(json.dumps(summary, indent=2))

print(f"\n{'='*60}")
print(f"  P&R 完成")
print(f"  综合通过: {len(synth_passed)}")
print(f"  P&R 通过: {pnr_pass}")
print(f"  P&R 失败: {pnr_fail}")
print(f"{'='*60}")
PYEOF
