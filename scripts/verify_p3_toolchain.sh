#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work_dir="$(mktemp -d)"
trap 'rm -rf "$work_dir"' EXIT
python_bin="${P3_PYTHON:-python3}"

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "P3 regression requires '$1' on PATH" >&2
        exit 2
    fi
}

require_command "$python_bin"
require_command lake
require_command lean
require_command iverilog
require_command vvp
require_command g++
"$python_bin" -c 'import pytest' >/dev/null

cd "$repo_root"
export PYTHONPATH="$repo_root${PYTHONPATH:+:$PYTHONPATH}"

lake build Sparkle
# The P3 IR is also consumed by the SystemVerilog import/JIT path. Keep this
# target in the gate so new statements or symbolic dimensions cannot silently
# break SVParser lowering while the Lean-to-Verilog path still passes.
lake build Tools.SVParser.Lower
"$python_bin" -m pytest -q --junitxml="$work_dir/pytest.xml"
"$python_bin" - "$work_dir/pytest.xml" <<'PY'
import sys
import xml.etree.ElementTree as ET

root = ET.parse(sys.argv[1]).getroot()
skipped = sum(int(node.attrib.get("skipped", 0)) for node in root.iter("testsuite"))
if skipped:
    raise SystemExit(f"P3 regression requires zero skipped tests, observed {skipped}")
PY

lake env lean Tests/SymbolicParameterSim.lean > "$work_dir/symbolic_parameter_lean.log"
grep -q "SYMBOLIC_PARAMETER_LEAN_SIM_PASS" "$work_dir/symbolic_parameter_lean.log"
lake env lean Tests/SignedHelpers.lean > "$work_dir/signed_helpers_lean.log"
grep -q "SIGNED_HELPER_LEAN_SIM_PASS" "$work_dir/signed_helpers_lean.log"
bash scripts/verify_signed_helpers.sh
lake exe verilog-tests
lake build Tests.SymbolicParameterEmit
lake env lean p3_tests/CppSimSpecializations.lean
bash scripts/verify_symbolic_parameters.sh

if [[ "${P3_RUN_ORFS:-0}" == "1" ]]; then
    require_command docker
    "$python_bin" scripts/verify_p3_ppa_orfs.py --pnr
fi

echo "P3_TOOLCHAIN_REGRESSION_PASS"
