/-
  VerilogEval Prob024: Half Adder

  NL Description:
  Implement a module named TopModule with the following interface.
  All input and output ports are one bit unless otherwise specified.
   - input  a
   - input  b
   - output sum
   - output cout
  The module should implement a half adder. A half adder adds two bits
  (with no carry-in) and produces a sum and carry-out.

  Reference Verilog:
  module RefModule (input a, input b, output sum, output cout);
    assign {cout, sum} = a+b;
  endmodule
-/

import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Half adder: adds two 1-bit inputs, returns bundled (sum, cout). -/
def prob024_hadd {dom : DomainConfig}
    (a b : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1) :=
  let sum  := a ^^^ b         -- XOR for sum
  let cout := a &&& b         -- AND for carry
  bundle2 sum cout

#synthesizeVerilog prob024_hadd
