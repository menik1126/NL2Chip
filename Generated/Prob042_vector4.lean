import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Sign-extend an 8-bit value to 32 bits by replicating the sign bit (bit 7) 24 times. -/
def prob042_vector4 {dom : DomainConfig}
    (input : Signal dom (BitVec 8)) : Signal dom (BitVec 32) :=
  let signBit := input.map (BitVec.extractLsb' 7 1 ·)
  let isNeg := signBit === 1#1
  let padOnes := BitVec.ofNat 24 0xFFFFFF ++ input
  let padZeros := 0#24 ++ input
  Signal.mux isNeg padOnes padZeros

#synthesizeVerilog prob042_vector4
