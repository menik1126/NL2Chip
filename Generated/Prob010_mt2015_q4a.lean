/-
  VerilogEval Prob010: mt2015_q4a

  NL Description:
  Implement a module named TopModule with the following interface.
  All input and output ports are one bit unless otherwise specified.
   - input  x
   - input  y
   - output z
  The module should implement the boolean function z = (x^y) & x.

  Reference Verilog:
  module RefModule (input x, input y, output z);
    assign z = (x^y) & x;
  endmodule
-/

import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Boolean function: z = (x^y) & x -/
def prob010_mt2015_q4a {dom : DomainConfig}
    (x y : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  (x ^^^ y) &&& x

#synthesizeVerilog prob010_mt2015_q4a
