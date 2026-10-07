#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RESULTS_DIR="${RESULTS_DIR:-$ROOT/results/expanded_benchmarks}"
MODEL="${MODEL:-claude-sonnet-4-5-20250929}"
WORKERS="${WORKERS:-8}"
BASELINE_WORKERS="${BASELINE_WORKERS:-4}"
MAX_ITERS="${MAX_ITERS:-5}"

export PATH="$HOME/.local/share/mamba/bin:$HOME/.elan/bin:$HOME/.local/bin:$PATH"
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY

cd "$ROOT"
if [ -d .venv ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

mkdir -p "$RESULTS_DIR"

run_main() {
  local dataset="$1"
  shift
  python agent/search.py \
    --dataset "$dataset" \
    --workers "$WORKERS" \
    --model "$MODEL" \
    --results-dir "$RESULTS_DIR" \
    --resume --resume-mode completed \
    "$@"
}

run_baseline() {
  local dataset="$1"
  shift
  python experiments/baseline_verilog_iterative.py \
    --dataset "$dataset" \
    --workers "$BASELINE_WORKERS" \
    --model "$MODEL" \
    --max-iters "$MAX_ITERS" \
    --results-dir "$RESULTS_DIR" \
    --resume \
    "$@"
}

case "${1:-all}" in
  main)
    run_main verilogeval
    run_main rtllm
    run_main resbench
    run_main cvdp --filter 'cid003|cid004|cid016'
    run_main realbench --limit 60
    ;;
  baseline)
    run_baseline verilogeval
    run_baseline rtllm
    run_baseline resbench
    run_baseline cvdp --filter 'cid003|cid004|cid016'
    run_baseline realbench --limit 60
    ;;
  all)
    "$0" main
    "$0" baseline
    ;;
  *)
    echo "Usage: $0 [main|baseline|all]" >&2
    exit 2
    ;;
esac
