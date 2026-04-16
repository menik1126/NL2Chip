import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 32-bit Galois LFSR with taps at positions 32, 22, 2, 1.
    Shifts right; tapped bits XOR with q[0]. Sync reset to 32'h1.
    Tap mask: 0x80200003 (bits 31, 21, 1, 0) -/
def prob082_lfsr32 {dom : DomainConfig}
    (reset : Signal dom Bool) : Signal dom (BitVec 32) :=
  Signal.loop fun (q : Signal dom (BitVec 32)) =>
    -- Galois LFSR: shift right, XOR tapped positions with q[0]
    -- q_next = (q >> 1) XOR (0x80200003 if q[0] == 1 else 0)
    let shifted := q >>> 1#32
    let lsb := q &&& (1#32 : BitVec 32)
    let fb := lsb === (1#32 : BitVec 32)
    let q_next := Signal.mux fb (shifted ^^^ (0x80200003#32 : BitVec 32)) shifted
    let nextVal := Signal.mux reset (Signal.pure 1#32) q_next
    Signal.register 1#32 nextVal

#synthesizeVerilog prob082_lfsr32
