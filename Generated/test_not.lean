/-
  VerilogEval Prob005: NOT Gate

  NL Description:
  Implement a module named TopModule with the following interface.
  All input and output ports are one bit unless otherwise specified.
   - input  in
   - output out
  The module should implement a NOT gate.

  Reference Verilog:
  module RefModule (input in, output out);
    assign out = ~in;
  endmodule
-/

import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- NOT gate: inverts a 1-bit input signal. -/
def prob005_notgate {dom : DomainConfig}
    (input : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  ~~~input

#synthesizeVerilog prob005_notgate
