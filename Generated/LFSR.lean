import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Linear Feedback Shift Register: 4-bit LFSR with feedback from bits 3 and 2. -/
def LFSR {dom : DomainConfig}
    (rst : Signal dom Bool) : Signal dom (BitVec 4) :=
  Signal.loop fun (out : Signal dom (BitVec 4)) =>
    -- Extract bit 3 and bit 2 by shifting and masking
    let bit3_shifted := out >>> 3#4
    let bit2_shifted := out >>> 2#4
    let bit3_masked := bit3_shifted &&& 1#4
    let bit2_masked := bit2_shifted &&& 1#4
    -- XOR and invert
    let xor_result := bit3_masked ^^^ bit2_masked
    let feedback := ~~~xor_result &&& 1#4
    -- Shift left and insert feedback
    let shifted := (out <<< 1#4) ||| feedback
    let nextVal := Signal.mux rst (Signal.pure 0#4) shifted
    Signal.register 0#4 nextVal

#synthesizeVerilog LFSR
