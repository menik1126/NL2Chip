#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
MAIN_ROOT="${MAIN_ROOT:-$ROOT}"
KEY_ENV="${KEY_ENV:-${MAIN_ROOT}/key.env}"
HARNESS="${HARNESS:-anthropic-api}"
MODEL="${MODEL:-claude-sonnet-4.5}"
WORKERS="${WORKERS:-4}"
RESULTS_DIR="${RESULTS_DIR:-${ROOT}/results/p3_cvdp12_$(date +%Y%m%d_%H%M%S)}"
CVDP_HARNESS_PROFILE="${CVDP_HARNESS_PROFILE:-race-safe-v1}"
if [[ -x "${ROOT}/.venv/bin/python" ]]; then
    DEFAULT_PYTHON="${ROOT}/.venv/bin/python"
else
    DEFAULT_PYTHON="${MAIN_ROOT}/.venv/bin/python"
fi
PYTHON_BIN="${PYTHON_BIN:-${DEFAULT_PYTHON}}"

# race-safe-v1 adds one simulator-step settle after cocotb edge waits and
# aligns Timer-based reset release to the clock low phase. It does not change
# stimulus values, expected values, or assertions. Use `official` for the
# unchanged-harness ablation. Legacy CVDP_* flags remain explicit overrides.
export CVDP_HARNESS_PROFILE

if [[ ! -x "${PYTHON_BIN}" ]]; then
    echo "Missing Python environment: ${PYTHON_BIN}" >&2
    exit 2
fi

COMMON_ARGS=(
    --dataset cvdp
    --cvdp-harness-profile "${CVDP_HARNESS_PROFILE}"
    --problem-file "${ROOT}/experiments/cvdp_parameterized_12.txt"
    --model "${MODEL}"
    --max-turns 10
    --max-tokens 16384
    --prompt-profile cvdp-skill-fewshot
    --results-dir "${RESULTS_DIR}"
    --harness "${HARNESS}"
    --native-parameter-sweep
    --native-formal-policy off
    --native-cppsim-policy off
    --native-ppa-policy off
    --workers "${WORKERS}"
    --sim-feedback
    --sim-feedback-max-iters 9
    --sim-feedback-turn-budget 90
    --sim-feedback-turns-per-iter 10
    --sim-feedback-patience 0
    --sim-feedback-rewrite-patience 2
    --sim-feedback-max-candidates 3
    --guided-search
    --disable-guided-self-test
    --candidate-search-max 3
    --candidate-stagnation-patience 2
    --search-total-turn-budget 100
)

case "${HARNESS}" in
    anthropic-api)
        if [[ ! -f "${KEY_ENV}" ]]; then
            echo "Missing KEY_ENV: ${KEY_ENV}" >&2
            exit 2
        fi
        set -a
        source "${KEY_ENV}"
        set +a
        if [[ -z "${ANTHROPIC_BASE_URL:-}" ]]; then
            echo "ANTHROPIC_BASE_URL is not configured by ${KEY_ENV}" >&2
            exit 2
        fi
        if [[ -z "${ANTHROPIC_AUTH_TOKEN:-${ANTHROPIC_API_KEY:-${API_KEY:-}}}" ]]; then
            echo "No Anthropic API credential is configured by ${KEY_ENV}" >&2
            exit 2
        fi
        COMMON_ARGS+=(--key-env "${KEY_ENV}")
        ;;
    codex-agent)
        CODEX_BIN="${CODEX_BIN:-$(command -v codex || true)}"
        ARCHON_SRC="${ARCHON_SRC:-/hq/archon-official/src}"
        CODEX_EFFORT="${CODEX_EFFORT:-ultra}"
        if [[ -z "${CODEX_BIN}" || ! -x "${CODEX_BIN}" ]]; then
            echo "Missing Codex executable; set CODEX_BIN" >&2
            exit 2
        fi
        if [[ ! -d "${ARCHON_SRC}" ]]; then
            echo "Missing Archon source directory: ${ARCHON_SRC}" >&2
            exit 2
        fi
        COMMON_ARGS+=(
            --archon-src "${ARCHON_SRC}"
            --codex-bin "${CODEX_BIN}"
            --codex-effort "${CODEX_EFFORT}"
            --no-codex-chat-proxy
        )
        ;;
    *)
        echo "Unsupported HARNESS for this experiment: ${HARNESS}" >&2
        exit 2
        ;;
esac

exec "${PYTHON_BIN}" -m cktlean.run "${COMMON_ARGS[@]}"
