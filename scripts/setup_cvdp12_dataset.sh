#!/usr/bin/env bash
set -euo pipefail

# Install the 12-task CVDP true-parameter subset bundled with this repository
# into the standard dataset path searched by agent/dataset.py.
#
# Usage:
#   ./scripts/setup_cvdp12_dataset.sh
#   DATASET_ROOT=/path/to/benchmarks/cvdp-benchmark-dataset ./scripts/setup_cvdp12_dataset.sh

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATASET_ROOT="${DATASET_ROOT:-$ROOT_DIR/benchmarks/cvdp-benchmark-dataset}"
REQUIRED_FILE="cvdp_v1.1.0_nonagentic_code_generation_no_commercial.jsonl"
SOURCE_FILE="$ROOT_DIR/experiments/cvdp12_dataset/$REQUIRED_FILE"
TARGET_FILE="$DATASET_ROOT/$REQUIRED_FILE"

if [[ ! -f "$SOURCE_FILE" ]]; then
  echo "[cvdp12-dataset] missing bundled subset: $SOURCE_FILE" >&2
  exit 1
fi

mkdir -p "$DATASET_ROOT"
cp "$SOURCE_FILE" "$TARGET_FILE"

echo "[cvdp12-dataset] installed 12-task subset: $TARGET_FILE"
