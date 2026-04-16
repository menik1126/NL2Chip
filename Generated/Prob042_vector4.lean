import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Sign-extend an 8-bit input to 32 bits by replicating the sign bit (bit[7]) 24 times. -/
def prob042_vector4 {dom : DomainConfig}
    (in_ : Signal dom (BitVec 8)) : Signal dom (BitVec 32) :=
  -- Extract sign bit (bit 7)
  let signBit := in_.map (BitVec.extractLsb' 7 1 ·)  -- 1 bit
  -- Build 24 copies via doubling: 1→2→4→8→16, then 16+8=24
  let sign2  := signBit ++ signBit   -- 2 bits
  let sign4  := sign2   ++ sign2     -- 4 bits
  let sign8  := sign4   ++ sign4     -- 8 bits
  let sign16 := sign8   ++ sign8     -- 16 bits
  let sign24 := sign16  ++ sign8     -- 24 bits
  -- Concatenate 24 sign bits with the original 8-bit value
  sign24 ++ in_

#synthesizeVerilog prob042_vector4
