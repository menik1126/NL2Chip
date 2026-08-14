#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAIN_ROOT="${MAIN_ROOT:-/home/sgli/work/NL2Chip}"
KEY_ENV="${KEY_ENV:-${MAIN_ROOT}/key.env}"
MODEL="${MODEL:-claude-sonnet-4.5}"
WORKERS="${WORKERS:-4}"
RESULTS_DIR="${RESULTS_DIR:-${ROOT}/results/p3_cvdp12_$(date +%Y%m%d_%H%M%S)}"
PYTHON_BIN="${PYTHON_BIN:-${MAIN_ROOT}/.venv/bin/python}"
export LAKE_PATH="${LAKE_PATH:-/home/sgli/.elan/bin/lake}"

if [[ ! -f "${KEY_ENV}" ]]; then
    echo "Missing KEY_ENV: ${KEY_ENV}" >&2
    exit 2
fi
if [[ ! -x "${PYTHON_BIN}" ]]; then
    echo "Missing Python environment: ${PYTHON_BIN}" >&2
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

exec "${PYTHON_BIN}" -m cktarchon.run \
    --dataset cvdp \
    --problem-file "${ROOT}/experiments/cvdp_parameterized_12.txt" \
    --model "${MODEL}" \
    --max-turns 10 \
    --max-tokens 16384 \
    --prompt-profile cvdp-skill-fewshot \
    --results-dir "${RESULTS_DIR}" \
    --harness anthropic-api \
    --key-env "${KEY_ENV}" \
    --native-parameter-sweep \
    --native-formal-policy off \
    --native-cppsim-policy off \
    --native-ppa-policy off \
    --workers "${WORKERS}" \
    --sim-feedback \
    --sim-feedback-max-iters 9 \
    --sim-feedback-turn-budget 90 \
    --sim-feedback-turns-per-iter 10 \
    --sim-feedback-patience 0 \
    --sim-feedback-rewrite-patience 2 \
    --sim-feedback-max-candidates 3 \
    --guided-search \
    --disable-guided-self-test \
