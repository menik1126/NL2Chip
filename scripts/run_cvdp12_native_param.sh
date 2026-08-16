#!/usr/bin/env bash
set -euo pipefail

# Run the 12-task CVDP true-parameter subset for native symbolic-parameter
# debugging.  Override variables from the shell as needed, for example:
#
#   KEY_ENV=/path/to/key.env WORKERS=16 ./scripts/run_cvdp12_native_param.sh
#   MODEL=gpt-5.6-sol HARNESS=codex-agent ./scripts/run_cvdp12_native_param.sh
#   CVDP_LOCAL_GUARDRAILS=1 CVDP_VERIFIED_IDIOMS=1 \
#     MODEL=claude-opus-4-6 HARNESS=anthropic-api ./scripts/run_cvdp12_native_param.sh
#   ARCHON_SRC=/path/to/science-mango/src NO_CODEX_CHAT_PROXY=1 \
#     MODEL=gpt-5.6-sol HARNESS=codex-agent ./scripts/run_cvdp12_native_param.sh
#
# This repository includes the 12 relevant CVDP JSONL rows. If the dataset path
# has not been installed yet, run:
#   ./scripts/setup_cvdp12_dataset.sh

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
MODEL="${MODEL:-claude-sonnet-4.5}"
HARNESS="${HARNESS:-anthropic-api}"
ARCHON_SRC="${ARCHON_SRC:-}"
NO_CODEX_CHAT_PROXY="${NO_CODEX_CHAT_PROXY:-0}"
KEY_ENV="${KEY_ENV:-$ROOT_DIR/key.env}"
PROBLEM_FILE="${PROBLEM_FILE:-$ROOT_DIR/experiments/problem_lists/cvdp12_true_parameter_subset.txt}"
RESULTS_DIR="${RESULTS_DIR:-$ROOT_DIR/results/cvdp12_native_param_$(date +%Y%m%d_%H%M%S)}"
WORKERS="${WORKERS:-12}"
MAX_TURNS="${MAX_TURNS:-10}"
SIM_FEEDBACK_MAX_ITERS="${SIM_FEEDBACK_MAX_ITERS:-10}"
SIM_FEEDBACK_TURN_BUDGET="${SIM_FEEDBACK_TURN_BUDGET:-90}"
SIM_FEEDBACK_TURNS_PER_ITER="${SIM_FEEDBACK_TURNS_PER_ITER:-10}"
SIM_FEEDBACK_PATIENCE="${SIM_FEEDBACK_PATIENCE:-2}"
PROMPT_PROFILE="${PROMPT_PROFILE:-cvdp-skill-fewshot}"
CVDP_LOCAL_GUARDRAILS="${CVDP_LOCAL_GUARDRAILS:-0}"
CVDP_VERIFIED_IDIOMS="${CVDP_VERIFIED_IDIOMS:-0}"
CVDP_GENERATED_DEV_FEEDBACK="${CVDP_GENERATED_DEV_FEEDBACK:-0}"
CVDP_GENERATED_DEV_SEED="${CVDP_GENERATED_DEV_SEED:-0xC0D32026}"

mkdir -p "$RESULTS_DIR"

extra_args=()
if [[ "$HARNESS" == "codex-agent" || "$HARNESS" == "archon-native" ]]; then
  if [[ -z "$ARCHON_SRC" ]]; then
    echo "[cvdp12-native-param] error: $HARNESS requires ARCHON_SRC=/path/to/official/archon/src" >&2
    exit 2
  fi
  if [[ ! -d "$ARCHON_SRC" ]]; then
    echo "[cvdp12-native-param] error: ARCHON_SRC directory does not exist: $ARCHON_SRC" >&2
    exit 2
  fi
  extra_args+=(--archon-src "$ARCHON_SRC")
fi
case "$NO_CODEX_CHAT_PROXY" in
  0|false|FALSE|no|NO|"")
    ;;
  1|true|TRUE|yes|YES)
    extra_args+=(--no-codex-chat-proxy)
    ;;
  *)
    echo "[cvdp12-native-param] error: NO_CODEX_CHAT_PROXY must be 0/1 or false/true" >&2
    exit 2
    ;;
esac
case "$CVDP_LOCAL_GUARDRAILS" in
  0|false|FALSE|no|NO|"")
    ;;
  1|true|TRUE|yes|YES)
    extra_args+=(--cvdp-local-guardrails)
    ;;
  *)
    echo "[cvdp12-native-param] error: CVDP_LOCAL_GUARDRAILS must be 0/1 or false/true" >&2
    exit 2
    ;;
esac
case "$CVDP_VERIFIED_IDIOMS" in
  0|false|FALSE|no|NO|"")
    ;;
  1|true|TRUE|yes|YES)
    extra_args+=(--cvdp-verified-idioms)
    ;;
  *)
    echo "[cvdp12-native-param] error: CVDP_VERIFIED_IDIOMS must be 0/1 or false/true" >&2
    exit 2
    ;;
esac
case "$CVDP_GENERATED_DEV_FEEDBACK" in
  0|false|FALSE|no|NO|"")
    ;;
  1|true|TRUE|yes|YES)
    if [[ ! "$CVDP_GENERATED_DEV_SEED" =~ ^(0[xX][0-9a-fA-F]+|[0-9]+)$ ]]; then
      echo "[cvdp12-native-param] error: CVDP_GENERATED_DEV_SEED must be decimal or 0x-prefixed hexadecimal" >&2
      exit 2
    fi
    extra_args+=(--cvdp-generated-dev-feedback --cvdp-generated-dev-seed "$CVDP_GENERATED_DEV_SEED")
    ;;
  *)
    echo "[cvdp12-native-param] error: CVDP_GENERATED_DEV_FEEDBACK must be 0/1 or false/true" >&2
    exit 2
    ;;
esac
if [[ "${NO_REPL:-0}" == "1" ]]; then
  extra_args+=(--no-repl)
fi
if [[ -n "${LEAN_BIN_DIR:-}" ]]; then
  export PATH="$LEAN_BIN_DIR:$PATH"
fi

echo "[cvdp12-native-param] root=$ROOT_DIR"
echo "[cvdp12-native-param] problem_file=$PROBLEM_FILE"
echo "[cvdp12-native-param] results_dir=$RESULTS_DIR"
echo "[cvdp12-native-param] model=$MODEL harness=$HARNESS workers=$WORKERS"
if [[ -n "$ARCHON_SRC" ]]; then
  echo "[cvdp12-native-param] archon_src=$ARCHON_SRC"
fi
echo "[cvdp12-native-param] no_codex_chat_proxy=$NO_CODEX_CHAT_PROXY"
echo "[cvdp12-native-param] cvdp_local_guardrails=$CVDP_LOCAL_GUARDRAILS cvdp_verified_idioms=$CVDP_VERIFIED_IDIOMS"
echo "[cvdp12-native-param] cvdp_generated_dev_feedback=$CVDP_GENERATED_DEV_FEEDBACK cvdp_generated_dev_seed=$CVDP_GENERATED_DEV_SEED"

"$PYTHON_BIN" -m cktarchon.run \
  --dataset cvdp \
  --problem-file "$PROBLEM_FILE" \
  --model "$MODEL" \
  --harness "$HARNESS" \
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
