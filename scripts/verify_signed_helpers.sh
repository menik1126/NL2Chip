#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

cd "$repo_root"
lake env lean Tests/SignedHelperEmit.lean > "$work_dir/signed_helpers.sv"
grep -q '\$signed' "$work_dir/signed_helpers.sv"
iverilog -g2012 \
    -s signed_helper_behavior_tb \
    -o "$work_dir/signed_helper_behavior" \
    "$work_dir/signed_helpers.sv" \
    Tests/SignedHelperBehavior.v
vvp "$work_dir/signed_helper_behavior"
