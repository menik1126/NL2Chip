/-
  VerilogEval Prob026: Always Block 1

  NL Description:
  Implement a module named TopModule with the following interface.
  All input and output ports are one bit unless otherwise specified.
   - input  a
   - input  b
   - output out_assign
   - output out_alwaysblock
  The module should implement an AND gate using both an assign statement
  and a combinational always block.

  Reference Verilog:
  module RefModule (
    input a,
    input b,
    output out_assign,
    output reg out_alwaysblock
  );
    assign out_assign = a & b;
    always @(*) out_alwaysblock = a & b;
  endmodule
-/

import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- AND gate with two outputs (simulating assign and always block implementations). -/
def prob026_alwaysblock1 {dom : DomainConfig}
    (a b : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1) :=
  let and_result := a &&& b
  bundle2 and_result and_result

#synthesizeVerilog prob026_alwaysblock1
