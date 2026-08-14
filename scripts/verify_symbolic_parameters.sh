#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT

cd "$repo_root"
lake env lean Tests/SymbolicParameterEmit.lean > "$work_dir/symbolic_parameters.sv"
iverilog -g2012 \
    -s symbolic_parameter_behavior_tb \
    -o "$work_dir/symbolic_parameter_behavior" \
    "$work_dir/symbolic_parameters.sv" \
    Tests/SymbolicParameterBehavior.v
vvp "$work_dir/symbolic_parameter_behavior"
