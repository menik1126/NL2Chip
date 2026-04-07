/-
  VerilogEval Prob086: 5-bit Galois LFSR

  NL Description:
  Implement a module named TopModule with the following interface.
  All input and output ports are one bit unless otherwise specified.
   - input  clk
   - input  reset
   - output q (5 bits)
  The module should implement a 5-bit maximal-length Galois LFSR with taps
  at bit positions 5 and 3. The active-high synchronous reset should reset
  the LFSR output to 1. Assume all sequential logic is triggered on the
  positive edge of the clock.

  A Galois LFSR shifts right, where a bit position with a "tap" is XORed
  with the LSB output bit (q[0]) to produce its next value, while bit
  positions without a tap shift right unchanged.

  Reference Verilog:
  module RefModule (input clk, input reset, output reg [4:0] q);
    logic [4:0] q_next;
    always @(q) begin
      q_next = q[4:1];
      q_next[4] = q[0];
      q_next[2] ^= q[0];
    end
    always @(posedge clk) begin
      if (reset) q <= 5'h1;
      else       q <= q_next;
    end
  endmodule

  Note: Uses Signal.loop for feedback. Bit manipulation done via
  Signal.map with BitVec extraction and concatenation.
-/

import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 5-bit Galois LFSR with taps at positions 5 and 3.
    Shifts right; tapped bits XOR with q[0]. Sync reset to 1. -/
def prob086_lfsr5 {dom : DomainConfig}
    (reset : Signal dom Bool) : Signal dom (BitVec 5) :=
  Signal.loop fun (q : Signal dom (BitVec 5)) =>
    -- Galois LFSR: shift right, XOR tapped positions with q[0]
    -- q_next[4] = q[0],  q_next[3] = q[4],  q_next[2] = q[3]^q[0],
    -- q_next[1] = q[2],  q_next[0] = q[1]
    -- Equivalent to: if q[0]==1 then (q>>>1) ^ 0b10100 else (q>>>1)
    -- Tap mask 0x14 = 0b10100: bits 4 and 2 (taps at positions 5 and 3)
    let shifted := q >>> 1#5
    let lsb := q &&& (1#5 : BitVec 5)
    let fb := lsb === (1#5 : BitVec 5)
    let q_next := Signal.mux fb (shifted ^^^ (20#5 : BitVec 5)) shifted
    let nextVal := Signal.mux reset (Signal.pure 1#5) q_next
    Signal.register 1#5 nextVal

#synthesizeVerilog prob086_lfsr5
