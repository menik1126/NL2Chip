#!/usr/bin/env bash
set -euo pipefail

# Run the 12-task CVDP true-parameter subset for native symbolic-parameter
# debugging.  Override variables from the shell as needed, for example:
#
#   KEY_ENV=/path/to/key.env WORKERS=16 ./scripts/run_cvdp12_native_param.sh
#
# This repository includes the 12 relevant CVDP JSONL rows. If the dataset path
# has not been installed yet, run:
#   ./scripts/setup_cvdp12_dataset.sh

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
MODEL="${MODEL:-claude-sonnet-4.5}"
KEY_ENV="${KEY_ENV:-$ROOT_DIR/key.env}"
PROBLEM_FILE="${PROBLEM_FILE:-$ROOT_DIR/experiments/problem_lists/cvdp12_true_parameter_subset.txt}"
RESULTS_DIR="${RESULTS_DIR:-$ROOT_DIR/results/cvdp12_native_param_$(date +%Y%m%d_%H%M%S)}"
WORKERS="${WORKERS:-12}"
MAX_TURNS="${MAX_TURNS:-10}"
SIM_FEEDBACK_MAX_ITERS="${SIM_FEEDBACK_MAX_ITERS:-10}"
SIM_FEEDBACK_TURN_BUDGET="${SIM_FEEDBACK_TURN_BUDGET:-90}"
SIM_FEEDBACK_TURNS_PER_ITER="${SIM_FEEDBACK_TURNS_PER_ITER:-10}"
SIM_FEEDBACK_PATIENCE="${SIM_FEEDBACK_PATIENCE:-0}"
PROMPT_PROFILE="${PROMPT_PROFILE:-cvdp-skill-fewshot}"

mkdir -p "$RESULTS_DIR"

extra_args=()
if [[ "${NO_REPL:-0}" == "1" ]]; then
  extra_args+=(--no-repl)
fi
if [[ -n "${LEAN_BIN_DIR:-}" ]]; then
  export PATH="$LEAN_BIN_DIR:$PATH"
fi

echo "[cvdp12-native-param] root=$ROOT_DIR"
echo "[cvdp12-native-param] problem_file=$PROBLEM_FILE"
echo "[cvdp12-native-param] results_dir=$RESULTS_DIR"
echo "[cvdp12-native-param] model=$MODEL workers=$WORKERS"

"$PYTHON_BIN" -m cktarchon.run \
  --dataset cvdp \
  --problem-file "$PROBLEM_FILE" \
  --model "$MODEL" \
  --prompt-profile "$PROMPT_PROFILE" \
  --max-turns "$MAX_TURNS" \
  --sim-feedback \
  --sim-feedback-max-iters "$SIM_FEEDBACK_MAX_ITERS" \
  --sim-feedback-turn-budget "$SIM_FEEDBACK_TURN_BUDGET" \
  --sim-feedback-turns-per-iter "$SIM_FEEDBACK_TURNS_PER_ITER" \
  --sim-feedback-patience "$SIM_FEEDBACK_PATIENCE" \
  --workers "$WORKERS" \
  --key-env "$KEY_ENV" \
  --results-dir "$RESULTS_DIR" \
  "${extra_args[@]}"
