/-
  BitNet BitLinear — Scale Multiply — Signal DSL

  Fixed-point scale: (acc48 × scale32) >>> 24, truncated to 32 bits.
  Uses 80-bit intermediate (mulProductBits = 48 + 32).
-/

import cktlean.Core.Signal
import cktlean.Core.Domain
import Examples.BitNet.Config
import Examples.BitNet.SignalHelpers

namespace cktlean.Examples.BitNet.BitLinear

open cktlean.Core.Signal
open cktlean.Core.Domain
open cktlean.Examples.BitNet.SignalHelpers

variable {dom : DomainConfig}

/-- Fixed-point scale multiply: (acc × scale) >>> 24, result in 32 bits.
    acc is 48-bit accumulator, scale is 32-bit Q8.24.
    Intermediate product is 80 bits. -/
def scaleMultiplySignal (acc : Signal dom (BitVec 48)) (scale : Signal dom (BitVec 32))
    : Signal dom (BitVec 32) :=
  -- Sign-extend acc to 80 bits (add 32 bits)
  let accExt := signExtendSignal 32 acc
  -- Sign-extend scale to 80 bits (add 48 bits)
  let scaleExt := signExtendSignal 48 scale
  -- Multiply in 80-bit domain
  let prod := accExt * scaleExt
  -- Extract bits [55:24] = ASR 24 + truncate to 32 bits
  prod.map (BitVec.extractLsb' 24 32 ·)

end cktlean.Examples.BitNet.BitLinear
