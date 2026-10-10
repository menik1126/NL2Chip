/-
  BitNet BitLinear Core — Signal DSL

  Pipelined BitLinear layer using Signal DSL.
  Core operations (MAC stage, adder tree) are in SignalHelpers.
  This module provides the top-level BitLinear function.
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

/-- Top-level pipelined BitLinear layer (Signal DSL).
    Applies ternary weights to activations via MAC stage + adder tree.
    Pipeline registers not yet supported (pipelineEvery ignored). -/
def bitLinearPipelinedSignal (weights : Array Int)
    (activations : Array (Signal dom (BitVec n)))
    : Signal dom (BitVec n) :=
  bitLinearSignal weights activations

end cktlean.Examples.BitNet.BitLinear
