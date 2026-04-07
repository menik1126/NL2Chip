/-
  VerilogEval Prob027: Full Adder

  NL Description:
  Implement a module named TopModule with the following interface.
  All input and output ports are one bit unless otherwise specified.
   - input  a
   - input  b
   - input  cin
   - output sum
   - output cout
  The module should implement a full adder. A full adder adds three bits
  (including carry-in) and produces a sum and carry-out.

  Reference Verilog:
  module RefModule (input a, input b, input cin, output sum, output cout);
    assign {cout, sum} = a+b+cin;
  endmodule
-/

import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Full adder: adds three 1-bit inputs (a, b, cin), returns bundled (sum, cout). -/
def prob027_fadd {dom : DomainConfig}
    (a b cin : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1) :=
  let sum  := a ^^^ b ^^^ cin
  let cout := (a &&& b) ||| (a &&& cin) ||| (b &&& cin)
  bundle2 sum cout

#synthesizeVerilog prob027_fadd
