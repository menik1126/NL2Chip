/-
  VerilogEval Prob031: D Flip-Flop

  NL Description:
  Implement a module named TopModule with the following interface.
  All input and output ports are one bit unless otherwise specified.
   - input clk
   - input d
   - output q
  The module should implement a single D flip-flop.
  Assume all sequential logic is triggered on the positive edge of the clock.

  Reference Verilog:
  module RefModule (input clk, input d, output reg q);
    initial q = 1'hx;
    always @(posedge clk) q <= d;
  endmodule

  Note: In Sparkle, the clock is implicit in DomainConfig.
  The register's initial value is set to 0 (Verilog uses 'x' which
  has no direct Sparkle equivalent).
-/

import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- D flip-flop: delays input by one clock cycle. -/
def prob031_dff {dom : DomainConfig}
    (d : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  Signal.register 0#1 d

#synthesizeVerilog prob031_dff
