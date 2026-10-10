/-
  BitNet Layers — ReLU² (Squared ReLU) — Signal DSL

  Implements max(0,x)² in Q16.16 fixed-point using Signal combinators.
  Square gives Q32.32 (64-bit), shift right 16 → Q16.16 (32-bit).
-/

import cktlean.Core.Signal
import cktlean.Core.Domain
import Examples.BitNet.Config
import Examples.BitNet.SignalHelpers

namespace cktlean.Examples.BitNet.Layers

open cktlean.Core.Signal
open cktlean.Core.Domain
open cktlean.Examples.BitNet.SignalHelpers

variable {dom : DomainConfig}

/-- ReLU²: max(0,x)² in Q16.16 using Signal DSL.
    Extract sign bit → if negative: output 0.
    Otherwise: sign-extend to 64 bits, square, extract bits [47:16]. -/
def reluSqSignal (x : Signal dom (BitVec 32)) : Signal dom (BitVec 32) :=
  -- Extract sign bit (bit 31)
  let signBit := x.map (BitVec.extractLsb' 31 1 ·)
  let isNeg := signBit === 1#1
  -- Sign-extend input to 64 bits
  let xExt := signExtendSignal 32 x
  -- Square (64-bit × 64-bit → 64-bit, no overflow for 32-bit inputs)
  let squared := xExt * xExt
  -- Extract bits [47:16] = ASR 16 then truncate to 32 bits
  let shifted := squared.map (BitVec.extractLsb' 16 32 ·)
  -- If negative → 0, else → shifted result
  Signal.mux isNeg (Signal.pure 0#32) shifted

end cktlean.Examples.BitNet.Layers
