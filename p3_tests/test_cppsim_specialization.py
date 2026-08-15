from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = PROJECT_ROOT / "p3_tests" / "CppSimSpecializations.lean"
sys.path.insert(0, str(PROJECT_ROOT / "agent"))

from parameter_backends import (  # noqa: E402
    cppsim_policy_is_required_failure,
    run_cppsim_parameter_policy,
)


CPP_CASES = {
    "/tmp/p3_cppsim_xor_w3.h": """
        symbolicXor dut;
        dut._gen_lhs = 5; dut._gen_rhs = 3; dut.eval();
        return dut.out == ((5 ^ 3) & 0x7) ? 0 : 1;
    """,
    "/tmp/p3_cppsim_xor_w17.h": """
        symbolicXor dut;
        dut._gen_lhs = 0x12345; dut._gen_rhs = 0x05555; dut.eval();
        return dut.out == ((0x12345 ^ 0x05555) & 0x1ffff) ? 0 : 1;
    """,
    "/tmp/p3_cppsim_hierarchy_w17.h": """
        symbolicXorHierarchy dut;
        dut._gen_lhs = 0x12345; dut._gen_rhs = 0x05555; dut.eval();
        return dut.out == ((0x12345 ^ 0x05555) & 0x1ffff) ? 0 : 1;
    """,
    "/tmp/p3_cppsim_generate_w3.h": """
        symbolicGenerateNot dut;
        dut._gen_x = 0x5; dut.eval();
        return dut.out == ((~0x5) & 0x7) ? 0 : 1;
    """,
    "/tmp/p3_cppsim_generate_w17.h": """
        symbolicGenerateNot dut;
        dut._gen_x = 0x12345; dut.eval();
        return dut.out == ((~0x12345) & 0x1ffff) ? 0 : 1;
    """,
    "/tmp/p3_cppsim_pair_loop_w3.h": """
        symbolicPairLoop dut;
        dut.reset();
        dut.eval();
        if (dut.out != 0) return 1;
        dut._gen_x = 0x5; dut.eval(); dut.tick(); dut.eval();
        return dut.out == (0x5 << 3) ? 0 : 1;
    """,
    "/tmp/p3_cppsim_pair_loop_w17.h": """
        symbolicPairLoop dut;
        dut.reset();
        dut.eval();
        if (dut.out != 0) return 1;
        dut._gen_x = 0x12345; dut.eval(); dut.tick(); dut.eval();
        return dut.out == (0x12345ULL << 17) ? 0 : 1;
    """,
    "/tmp/p3_cppsim_memory_a2_d8.h": """
        symbolicMemory dut;
        dut.reset();
        dut._gen_writeAddr = 1; dut._gen_writeData = 0x5a;
        dut._gen_writeEnable = 1; dut._gen_readAddr = 1;
        dut.eval(); dut.tick(); dut._gen_writeEnable = 0; dut.eval();
        return dut.out == 0x5a ? 0 : 1;
    """,
    "/tmp/p3_cppsim_memory_a4_d17.h": """
        symbolicMemory dut;
        dut.reset();
        dut._gen_writeAddr = 9; dut._gen_writeData = 0x12345;
        dut._gen_writeEnable = 1; dut._gen_readAddr = 9;
        dut.eval(); dut.tick(); dut._gen_writeEnable = 0; dut.eval();
        return dut.out == 0x12345 ? 0 : 1;
    """,
}


@pytest.mark.skipif(
    shutil.which("lake") is None or shutil.which("g++") is None,
    reason="Lean or C++ compiler unavailable",
)
def test_parameterized_cppsim_specializes_compiles_and_runs_every_case(
    tmp_path: Path,
):
    emit = subprocess.run(
        ["lake", "env", "lean", str(FIXTURE)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = emit.stdout + emit.stderr
    assert emit.returncode == 0, output
    assert "PANIC" not in output

    for index, (header_name, body) in enumerate(CPP_CASES.items()):
        header = Path(header_name)
        assert header.exists(), header
        generated = header.read_text()
        assert "requires specialization" not in generated
        assert "parameter integer" not in generated

        driver = tmp_path / f"driver_{index}.cpp"
        binary = tmp_path / f"driver_{index}"
        driver.write_text(
            f'#include "{header}"\nint main() {{\n{body}\n}}\n',
            encoding="utf-8",
        )
        compile_result = subprocess.run(
            ["g++", "-std=c++17", "-O0", str(driver), "-o", str(binary)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert compile_result.returncode == 0, (
            header_name + "\n" + compile_result.stdout + compile_result.stderr
        )
        run_result = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=10
        )
        assert run_result.returncode == 0, header_name


@pytest.mark.skipif(
    shutil.which("lake") is None or shutil.which("g++") is None,
    reason="Lean or C++ compiler unavailable",
)
def test_cppsim_policy_records_per_case_coverage_and_wide_abi_limit(
    tmp_path: Path,
):
    source = tmp_path / "generic_source.lean"
    source.write_text(
        "import Sparkle.Compiler.Elab\n"
        "import Tests.SymbolicParameterCircuits\n",
        encoding="utf-8",
    )
    supported_cases = [
        {"parameters": {"W": 3}},
        {"parameters": {"W": 17}},
    ]
    complete = run_cppsim_parameter_policy(
        project_root=PROJECT_ROOT,
        lean_file=source,
        target_name="symbolicXor",
        case_requests=supported_cases,
        output_dir=tmp_path / "complete",
        required=True,
    )
    assert complete["status"] == "passed"
    assert complete["coverage"] == "all_public_configurations"
    assert complete["family_covered"] is True
    assert cppsim_policy_is_required_failure(complete) is False

    with_wide_case = run_cppsim_parameter_policy(
        project_root=PROJECT_ROOT,
        lean_file=source,
        target_name="symbolicXor",
        case_requests=[
            *supported_cases,
            {
                "parameters": {"W": 65},
                "unsupported_reason": (
                    "CppSim behavioral ABI currently supports packed ports up to 64 bits"
                ),
            },
        ],
        output_dir=tmp_path / "partial",
        required=True,
    )
    assert with_wide_case["status"] == "unsupported"
    assert with_wide_case["coverage"] == "partial_configuration_set"
    assert with_wide_case["family_covered"] is False
    assert with_wide_case["cases"][-1]["cppsim_status"] == "unsupported"
    assert cppsim_policy_is_required_failure(with_wide_case) is True


@pytest.mark.skipif(shutil.which("lake") is None, reason="Lean/lake unavailable")
def test_cppsim_command_rejects_missing_top_level_specialization():
    fixture = PROJECT_ROOT / "p3_tests" / "CppSimMissingSpecialization.lean"
    output = Path("/tmp/should_not_exist_cppsim.h")
    output.unlink(missing_ok=True)
    proc = subprocess.run(
        ["lake", "env", "lean", str(fixture)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert (
        "missing required top-level CppSim specialization" in combined
        or "was not retained as a module parameter" in combined
    )
    assert not output.exists()
