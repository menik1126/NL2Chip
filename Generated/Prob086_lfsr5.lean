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
