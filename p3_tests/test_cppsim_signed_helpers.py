from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = PROJECT_ROOT / "p3_tests" / "CppSimSignedHelpers.lean"


CPP_CASES = {
    "/tmp/p3_cppsim_signed_sext_w4.h": """
        signedHelperSignExtend dut;
        dut._gen_x = 0xd; dut.eval();
        return dut.out == 0xfd ? 0 : 1;
    """,
    "/tmp/p3_cppsim_signed_asr_w4.h": """
        signedHelperArithmeticShiftRight dut;
        dut._gen_x = 0xd; dut._gen_amount = 1; dut.eval();
        return dut.out == 0xe ? 0 : 1;
    """,
    "/tmp/p3_cppsim_signed_lt_w4.h": """
        signedHelperLT dut;
        dut._gen_lhs = 0xd; dut._gen_rhs = 2; dut.eval();
        if (!dut.out) return 1;
        dut._gen_lhs = 2; dut._gen_rhs = 0xd; dut.eval();
        return dut.out ? 1 : 0;
    """,
    "/tmp/p3_cppsim_signed_mul_wide_w4.h": """
        signedHelperMulWide dut;
        dut._gen_lhs = 0xd; dut._gen_rhs = 2; dut.eval();
        return dut.out == 0xfa ? 0 : 1;
    """,
    "/tmp/p3_cppsim_signed_mul_trunc_w4.h": """
        signedHelperMulTrunc dut;
        dut._gen_lhs = 0xd; dut._gen_rhs = 2; dut.eval();
        return dut.out == 0xa ? 0 : 1;
    """,
    "/tmp/p3_cppsim_signed_mul_shift_w4.h": """
        signedHelperMulShiftTrunc dut;
        dut._gen_lhs = 0xd; dut._gen_rhs = 2; dut._gen_amount = 2; dut.eval();
        return dut.out == 0xe ? 0 : 1;
    """,
    "/tmp/p3_cppsim_signed_saturate_w8.h": """
        signedHelperSaturate dut;
        dut._gen_lower = 0xf6; dut._gen_upper = 0x0a;
        dut._gen_value = 0x64; dut.eval();
        if (dut.out != 0x0a) return 1;
        dut._gen_value = 0x9c; dut.eval();
        if (dut.out != 0xf6) return 1;
        dut._gen_value = 0x05; dut.eval();
        return dut.out == 0x05 ? 0 : 1;
    """,
    "/tmp/p3_cppsim_signed_saturate_c8.h": """
        signedHelperSaturateC8 dut;
        dut._gen_value = 0x64; dut.eval();
        if (dut.out != 0x0a) return 1;
        dut._gen_value = 0x9c; dut.eval();
        return dut.out == 0xf6 ? 0 : 1;
    """,
}


@pytest.mark.skipif(
    shutil.which("lake") is None or shutil.which("g++") is None,
    reason="Lean or C++ compiler unavailable",
)
def test_cppsim_signed_helpers_match_two_complement_behavior(tmp_path: Path):
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
        generated = header.read_text(encoding="utf-8")
        assert "requires specialization" not in generated

        driver = tmp_path / f"signed_driver_{index}.cpp"
        binary = tmp_path / f"signed_driver_{index}"
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
