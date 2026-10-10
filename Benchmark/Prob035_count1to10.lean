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

import cktlean
import cktlean.Compiler.Elab

open cktlean.Core.Domain
open cktlean.Core.Signal

/-- Decade counter: counts 1 through 10 with synchronous reset to 1. -/
def prob035_count1to10 {dom : DomainConfig}
    (reset : Signal dom Bool) : Signal dom (BitVec 4) :=
  Signal.loop fun (q : Signal dom (BitVec 4)) =>
    let atMax := q === 10#4
    let shouldReset := reset ||| atMax
    let nextVal := Signal.mux shouldReset (Signal.pure 1#4) (q + 1#4)
    Signal.register 1#4 nextVal

#synthesizeVerilog prob035_count1to10
