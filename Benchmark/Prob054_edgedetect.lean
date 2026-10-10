/-
  VerilogEval Prob054: 8-bit Positive Edge Detector

  NL Description:
  Implement a module named TopModule with the following interface.
  All input and output ports are one bit unless otherwise specified.
   - input  clk
   - input  in    (8 bits)
   - output pedge (8 bits)
  The module should examine each bit in an 8-bit vector and detect when
  the input signal changes from 0 in one clock cycle to 1 the next
  (similar to positive edge detection). The output bit should be set
  the cycle after a 0 to 1 transition occurs.

  Reference Verilog:
  module RefModule (input clk, input [7:0] in, output reg [7:0] pedge);
    reg [7:0] d_last;
    always @(posedge clk) begin
      d_last <= in;
      pedge <= in & ~d_last;
    end
  endmodule

  Note: The reference uses two registers (d_last and pedge). In Sparkle,
  we use Signal.register for each stage.
-/

import cktlean
import cktlean.Compiler.Elab

open cktlean.Core.Domain
open cktlean.Core.Signal

/-- 8-bit positive edge detector: output is high for one cycle after
    each bit transitions from 0 to 1. -/
def prob054_edgedetect {dom : DomainConfig}
    (input : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  -- Register the previous input value
  let d_last := Signal.register 0#8 input
  -- Detect 0→1 transitions: current=1 AND previous=0
  let edges := input &&& (~~~d_last)
  -- Register the edge detection output (matches Verilog behavior)
  Signal.register 0#8 edges

#synthesizeVerilog prob054_edgedetect
