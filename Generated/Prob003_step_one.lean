/-
  VerilogEval Prob003: Step One

  NL Description:
  Implement a module named TopModule with the following interface.
  All input and output ports are one bit unless otherwise specified.
   - output one
  The module should always drive 1 (or logic high).

  Reference Verilog:
  module RefModule (output one);
    assign one = 1'b1;
  endmodule
-/

import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Module that always outputs logic high (1). -/
def prob003_step_one {dom : DomainConfig}
    : Signal dom (BitVec 1) :=
  Signal.pure 1#1

#synthesizeVerilog prob003_step_one
