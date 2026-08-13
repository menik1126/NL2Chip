from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


LEAN_SOURCE = r"""
import Sparkle

open Sparkle.Core.Domain Sparkle.Core.Signal

def nativeAdd {dom : DomainConfig} {W : Nat}
    (a b : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  a + b

def nativeConstant {W : Nat} : Signal Domain (BitVec W) :=
  Signal.pure (1 : BitVec W)

def nativeDerivedConstant {W : Nat} : Signal Domain (BitVec (W + 1)) :=
  Signal.pure (1 : BitVec (W + 1))

def nativeAllOnes {W : Nat} : Signal Domain (BitVec W) :=
  Signal.pure (BitVec.ofNat W (2 ^ W - 1))

def nativeSlice {dom : DomainConfig} {W Start Len : Nat}
    (x : Signal dom (BitVec W)) : Signal dom (BitVec Len) :=
  x.map (BitVec.extractLsb' Start Len ·)

def nativeRegister {dom : DomainConfig} {W : Nat}
    (d : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  Signal.register (0 : BitVec W) d

@[irreducible] def nativeRegisterChild {W : Nat}
    (d : Signal Domain (BitVec W)) : Signal Domain (BitVec W) :=
  Signal.register (0 : BitVec W) d

def nativeRegisterParent {W : Nat}
    (a : Signal Domain (BitVec W))
    (b : Signal Domain (BitVec (W + 1)))
    : Signal Domain (BitVec W × BitVec (W + 1)) :=
  bundle2 (nativeRegisterChild a) (nativeRegisterChild b)

def nativeMemory {dom : DomainConfig} {AW DW : Nat}
    (writeAddr : Signal dom (BitVec AW))
    (writeData : Signal dom (BitVec DW))
    (writeEnable : Signal dom Bool)
    (readAddr : Signal dom (BitVec AW)) : Signal dom (BitVec DW) :=
  Signal.memory writeAddr writeData writeEnable readAddr

#synthesizeVerilog nativeAdd parameters [W := 8]
#synthesizeVerilog nativeConstant parameters [W := 8]
#synthesizeVerilog nativeDerivedConstant parameters [W := 8]
#synthesizeVerilog nativeAllOnes parameters [W := 8]
#synthesizeVerilog nativeSlice parameters [W := 8, Start := 0, Len := 4]
#synthesizeVerilog nativeRegister parameters [W := 8]
#synthesizeVerilogDesign nativeRegisterParent parameters [W := 8]
#synthesizeVerilog nativeMemory parameters [AW := 4, DW := 8]
"""


UNSUPPORTED_SYMBOLIC_VALUE_SOURCE = r"""
import Sparkle

open Sparkle.Core.Domain Sparkle.Core.Signal

-- Native parameters currently cover hardware dimensions plus the common
-- all-ones mask.  Arbitrary value-level Nat programs must fail closed rather
-- than being frozen at the default parameter value.
def unsupportedSymbolicValue {W : Nat} : Signal Domain (BitVec W) :=
  Signal.pure (BitVec.ofNat W (W + 1))

#synthesizeVerilog unsupportedSymbolicValue parameters [W := 8]
"""


TESTBENCH = r"""
module tb;
  logic [2:0] a3, b3;
  logic [2:0] sum3;
  logic [16:0] a17, b17;
  logic [16:0] sum17;
  logic [256:0] a257, b257;
  logic [256:0] sum257;
  logic [2:0] one3;
  logic [16:0] one17;
  logic [3:0] derived_one4;
  logic [2:0] ones3;
  logic [16:0] ones17;
  logic [7:0] slice_in;
  logic [2:0] slice_out;
  logic [2:0] narrow_slice_in;
  logic [3:0] padded_slice_out;
  logic clk = 0;
  logic rst = 1;
  logic [2:0] reg_d3;
  logic [2:0] reg_q3;
  logic [2:0] parent_a3;
  logic [3:0] parent_b4;
  logic [6:0] parent_q7;
  logic [1:0] mem_wa;
  logic [2:0] mem_wd;
  logic mem_we;
  logic [1:0] mem_ra;
  logic [2:0] mem_rd;

  always #1 clk = ~clk;

  nativeAdd #(.W(3)) add3(
    ._gen_a(a3), ._gen_b(b3), .out(sum3));
  nativeAdd #(.W(17)) add17(
    ._gen_a(a17), ._gen_b(b17), .out(sum17));
  nativeAdd #(.W(257)) add257(
    ._gen_a(a257), ._gen_b(b257), .out(sum257));
  nativeConstant #(.W(3)) const3(.out(one3));
  nativeConstant #(.W(17)) const17(.out(one17));
  nativeDerivedConstant #(.W(3)) derived_const4(.out(derived_one4));
  nativeAllOnes #(.W(3)) all_ones3(.out(ones3));
  nativeAllOnes #(.W(17)) all_ones17(.out(ones17));
  nativeSlice #(.W(8), .Start(2), .Len(3)) slice3(
    ._gen_x(slice_in), .out(slice_out));
  nativeSlice #(.W(3), .Start(2), .Len(4)) padded_slice4(
    ._gen_x(narrow_slice_in), .out(padded_slice_out));
  nativeRegister #(.W(3)) reg3(
    ._gen_d(reg_d3), .clk(clk), .rst(rst), .out(reg_q3));
  nativeRegisterParent #(.W(3)) reg_parent3(
    ._gen_a(parent_a3), ._gen_b(parent_b4), .clk(clk), .rst(rst),
    .out(parent_q7));
  nativeMemory #(.AW(2), .DW(3)) mem3(
    ._gen_writeAddr(mem_wa), ._gen_writeData(mem_wd),
    ._gen_writeEnable(mem_we), ._gen_readAddr(mem_ra),
    .clk(clk), .rst(rst), .out(mem_rd));

  initial begin
    a3 = 3'd6;
    b3 = 3'd3;
    a17 = 17'd70000;
    b17 = 17'd60000;
    a257 = 257'd5;
    b257 = 257'd7;
    reg_d3 = 3'd5;
    parent_a3 = 3'b101;
    parent_b4 = 4'b1010;
    slice_in = 8'b10110110;
    narrow_slice_in = 3'b101;
    mem_wa = 2'd1;
    mem_wd = 3'd6;
    mem_we = 0;
    mem_ra = 2'd1;
    #1;
    if (sum3 !== 3'd1) $fatal(1, "W=3 addition was not truncated");
    if (sum17 !== 17'd130000) $fatal(1, "W=17 addition is wrong");
    if (sum257 !== 257'd12) $fatal(1, "W=257 addition is wrong");
    if (one3 !== 3'd1 || one17 !== 17'd1)
      $fatal(1, "parameter-sized constants are wrong");
    if (derived_one4 !== 4'd1)
      $fatal(1, "derived parameter-sized constant is wrong");
    if (ones3 !== 3'b111 || ones17 !== 17'h1ffff)
      $fatal(1, "width-dependent all-ones constant is wrong");
    if (slice_out !== 3'b101)
      $fatal(1, "symbolic slice offset/length is wrong");
    if (padded_slice_out !== 4'b0001)
      $fatal(1, "out-of-range extractLsb' was not zero padded");
    #1 rst = 0;
    #2;
    if (reg_q3 !== 3'd5) $fatal(1, "parameter-sized register is wrong");
    if (parent_q7 !== 7'b1011010)
      $fatal(1, "hierarchical parameterized registers lost clk/rst");
    mem_we = 1;
    #2 mem_we = 0;
    #2;
    if (mem_rd !== 3'd6) $fatal(1, "parameterized memory is wrong");
    $display("PASS native symbolic widths W=3/W=17 and AW=2/DW=3");
    $finish;
  end
endmodule
"""


def _generated_systemverilog(output: str) -> str:
    modules = re.findall(
        r"// Generated by Sparkle HDL.*?\nendmodule\n",
        output,
        flags=re.DOTALL,
    )
    assert len(modules) == 9, output
    return "\n".join(modules)


@pytest.mark.skipif(
    shutil.which("iverilog") is None or shutil.which("vvp") is None,
    reason="iverilog is required for native symbolic-width elaboration",
)
def test_one_generated_sv_module_elaborates_at_multiple_widths(tmp_path: Path):
    lean_file = tmp_path / "NativeSymbolicWidths.lean"
    lean_file.write_text(LEAN_SOURCE)
    lean = subprocess.run(
        ["lake", "env", "lean", str(lean_file)],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        timeout=120,
    )
    assert lean.returncode == 0, lean.stdout + lean.stderr

    generated_sv = _generated_systemverilog(lean.stdout)
    rtl_file = tmp_path / "native_symbolic_widths_rtl.sv"
    rtl_file.write_text(generated_sv)
    sv_file = tmp_path / "native_symbolic_widths.sv"
    sv_file.write_text(generated_sv + TESTBENCH)
    image = tmp_path / "native_symbolic_widths.vvp"
    compile_result = subprocess.run(
        ["iverilog", "-g2012", "-s", "tb", "-o", str(image), str(sv_file)],
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert compile_result.returncode == 0, compile_result.stdout + compile_result.stderr

    simulation = subprocess.run(
        ["vvp", str(image)],
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert simulation.returncode == 0, simulation.stdout + simulation.stderr
    assert "PASS native symbolic widths" in simulation.stdout

    # A simulator accepting the syntax is not enough: synthesize the same
    # emitted module twice after overriding its native parameter.  Keep this
    # optional so contributors without Yosys can still run the Icarus test.
    if shutil.which("yosys") is not None:
        for width in (3, 17, 257):
            synthesis = subprocess.run(
                [
                    "yosys",
                    "-Q",
                    "-p",
                    (
                        f"read_verilog -sv {rtl_file}; "
                        f"chparam -set W {width} nativeAdd; "
                        "prep -top nativeAdd; check"
                    ),
                ],
                text=True,
                capture_output=True,
                timeout=30,
            )
            assert synthesis.returncode == 0, synthesis.stdout + synthesis.stderr

    # Invalid overrides must fail through Sparkle's generated contract guard,
    # not crash the SV front end while it constructs a zero/negative range.
    for label, invalid_width in (("zero", 0), ("negative", -1)):
        invalid_sv = tmp_path / f"invalid_{label}.sv"
        invalid_sv.write_text(
            generated_sv
            + f"""
module invalid_{label};
  logic a, b, out;
  nativeAdd #(.W({invalid_width})) dut(
    ._gen_a(a), ._gen_b(b), .out(out));
endmodule
"""
        )
        invalid_image = tmp_path / f"invalid_{label}.vvp"
        invalid_compile = subprocess.run(
            [
                "iverilog",
                "-g2012",
                "-s",
                f"invalid_{label}",
                "-o",
                str(invalid_image),
                str(invalid_sv),
            ],
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert invalid_compile.returncode == 0, (
            invalid_compile.stdout + invalid_compile.stderr
        )
        invalid_sim = subprocess.run(
            ["vvp", str(invalid_image)],
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert invalid_sim.returncode != 0
        assert "Sparkle" in invalid_sim.stdout + invalid_sim.stderr


def test_arbitrary_symbolic_constant_value_fails_closed(tmp_path: Path):
    lean_file = tmp_path / "UnsupportedSymbolicValue.lean"
    lean_file.write_text(UNSUPPORTED_SYMBOLIC_VALUE_SOURCE)
    lean = subprocess.run(
        ["lake", "env", "lean", str(lean_file)],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        timeout=120,
    )
    diagnostics = lean.stdout + lean.stderr
    assert lean.returncode != 0, diagnostics
    assert "must be compile-time concrete" in diagnostics
