import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 5-bit Galois LFSR with taps at positions 5 and 3.
    Shifts right; tapped bits XOR with q[0]. Sync reset to 1. -/
def prob086_lfsr5 {dom : DomainConfig}
    (reset : Signal dom Bool) : Signal dom (BitVec 5) :=
  Signal.loop fun (q : Signal dom (BitVec 5)) =>
    -- Galois LFSR: shift right by 1, XOR tap mask with q[0]
    -- Taps at positions 5 and 3 (1-indexed) = bits 4 and 2 (0-indexed)
    -- Tap mask = 0b10100 = 20 = 0x14
    -- q_next = (q >> 1); if q[0] then q_next ^= 0x14
    let shifted := q >>> 1#5
    let lsb := q &&& (1#5 : BitVec 5)
    let fb := lsb === (1#5 : BitVec 5)
    let q_next := Signal.mux fb (shifted ^^^ (20#5 : BitVec 5)) shifted
    let nextVal := Signal.mux reset (Signal.pure 1#5) q_next
    Signal.register 1#5 nextVal

#synthesizeVerilog prob086_lfsr5
