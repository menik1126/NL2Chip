/-
  VerilogEval Prob012: XNOR Gate

  NL Description:
  Implement a module named TopModule with the following interface.
  All input and output ports are one bit unless otherwise specified.
   - input  a
   - input  b
   - output out
  The module should implement an XNOR gate.

  Reference Verilog:
  module RefModule (input a, input b, output out);
    assign out = ~(a^b);
  endmodule
-/

import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- XNOR gate: returns true when both inputs are equal. -/
def prob012_xnorgate {dom : DomainConfig}
    (a b : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  ~~~(a ^^^ b)

#synthesizeVerilog prob012_xnorgate
