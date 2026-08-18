from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_memory_combo_read_documentation_matches_supported_lowering():
    source = (PROJECT_ROOT / "Sparkle/Core/Signal.lean").read_text(
        encoding="utf-8"
    )
    start = source.index("Memory with combinational (same-cycle) reads.")
    end = source.index("private unsafe def memoryComboReadSparseImpl", start)
    documentation = source[start:end]

    assert "NOT synthesizable" not in documentation
    assert "Verilog backend synthesizes it" in documentation
    assert "asynchronous\n  read port" in documentation


def test_all_ones_preserves_symbolic_width_and_behavior(tmp_path: Path):
    for command in ("lake", "lean", "iverilog", "vvp"):
        assert shutil.which(command), f"required command is unavailable: {command}"

    built = subprocess.run(
        ["lake", "build", "Sparkle.Library.RTL"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert built.returncode == 0, built.stdout + built.stderr

    emitted = subprocess.run(
        ["lake", "env", "lean", "Tests/SymbolicAllOnesEmit.lean"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert emitted.returncode == 0, emitted.stdout + emitted.stderr
    assert "parameter integer W = 3" in emitted.stdout
    assert "W'(1)" in emitted.stdout

    generated = tmp_path / "symbolic_all_ones.sv"
    generated.write_text(emitted.stdout, encoding="utf-8")
    testbench = tmp_path / "symbolic_all_ones_tb.sv"
    testbench.write_text(
        """
module symbolic_all_ones_tb;
    logic [2:0] value3;
    wire out3;
    logic [4:0] value5;
    wire out5;

    symbolicAllOnes #(.W(3)) dut3 (._gen_value(value3), .out(out3));
    symbolicAllOnes #(.W(5)) dut5 (._gen_value(value5), .out(out5));

    initial begin
        value3 = 3'b111; value5 = 5'b11111; #1;
        if (out3 !== 1'b1 || out5 !== 1'b1) $fatal(1, "all-ones miss");
        value3 = 3'b110; value5 = 5'b01111; #1;
        if (out3 !== 1'b0 || out5 !== 1'b0) $fatal(1, "false all-ones hit");
        $display("SYMBOLIC_ALL_ONES_PASS");
        $finish;
    end
endmodule
""",
        encoding="utf-8",
    )
    executable = tmp_path / "symbolic_all_ones_tb"
    compiled = subprocess.run(
        [
            "iverilog", "-g2012", "-s", "symbolic_all_ones_tb",
            "-o", str(executable), str(generated), str(testbench),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    simulated = subprocess.run(
        ["vvp", str(executable)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert simulated.returncode == 0, simulated.stdout + simulated.stderr
    assert "SYMBOLIC_ALL_ONES_PASS" in simulated.stdout
