/-
  VerilogEval Prob008: Wire (Identity)

  NL Description:
  I would like you to implement a module named TopModule with the following
  interface. All input and output ports are one bit unless otherwise
  specified.
   - input  in
   - output out
  The module should assign the output port to the same value as the input
  port combinationally.

  Reference Verilog:
  module RefModule (input in, output out);
    assign out = in;
  endmodule
-/

import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Wire: passes input directly to output (identity function). -/
def prob008_m2014_q4h {dom : DomainConfig}
    (input : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  input

#synthesizeVerilog prob008_m2014_q4h
