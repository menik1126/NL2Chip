/-
  VerilogEval Prob020: 2-bit Equality Comparator

  NL Description:
  Implement a module named TopModule with the following interface.
  All input and output ports are one bit unless otherwise specified.
   - input  A (2 bits)
   - input  B (2 bits)
   - output z
  The module should implement a circuit that has two 2-bit inputs A[1:0]
  and B[1:0], and produces an output z. The value of z should be 1 if A = B,
  otherwise z should be 0.

  Reference Verilog:
  module RefModule (input [1:0] A, input [1:0] B, output z);
    assign z = A[1:0]==B[1:0];
  endmodule
-/

import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 2-bit equality comparator: outputs 1 if A equals B, 0 otherwise. -/
def prob020_mt2015_eq2 {dom : DomainConfig}
    (A B : Signal dom (BitVec 2)) : Signal dom Bool :=
  A === B

#synthesizeVerilog prob020_mt2015_eq2
