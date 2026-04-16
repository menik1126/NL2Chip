/-
  VerilogEval Prob035: Decade Counter (1 to 10)

  NL Description:
  Implement a module named TopModule with the following interface.
  All input and output ports are one bit unless otherwise specified.
   - input  clk
   - input  reset
   - output q (4 bits)
  The module should implement a decade counter that counts 1 through 10,
  inclusive. Assume all sequential logic is triggered on the positive edge
  of the clock. The reset input is active high synchronous, and should
  reset the counter to 1.

  Reference Verilog:
  module RefModule (input clk, input reset, output reg [3:0] q);
    always @(posedge clk)
      if (reset || q == 10) q <= 1;
      else q <= q+1;
  endmodule
-/

import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Decade counter: counts 1 through 10 with synchronous reset to 1. -/
def prob148_2013_q2afsm {dom : DomainConfig}
    (resetn : Signal dom Bool)
    (r : Signal dom (BitVec 3)) : Signal dom (BitVec 3) :=
  Signal.loop fun (q : Signal dom (BitVec 4)) =>
    let atMax := q === 10#4
    let shouldReset := reset ||| atMax
    let nextVal := Signal.mux shouldReset (Signal.pure 1#4) (q + 1#4)
    Signal.register 1#4 nextVal

#synthesizeVerilog prob148_2013_q2afsm
