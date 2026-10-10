/-
  BitNet BitLinear Dynamic — Signal DSL

  Dynamic (runtime) weight BitLinear layer using Signal DSL.
  Weight codes are 2-bit runtime signals decoded via mux:
    0b10 → +1 (pass-through), 0b00 → -1 (negate), else → 0.
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

/-- Dynamic BitLinear layer with runtime 2-bit weight codes.
    Decodes each weight, applies to activation, and sums via adder tree. -/
def dynamicBitLinearSignal (weightCodes : Array (Signal dom (BitVec 2)))
    (activations : Array (Signal dom (BitVec n)))
    : Signal dom (BitVec n) :=
  let decoded := dynamicMACStage weightCodes activations
  if decoded.size == 0 then Signal.pure 0
  else adderTree decoded

end cktlean.Examples.BitNet.BitLinear
