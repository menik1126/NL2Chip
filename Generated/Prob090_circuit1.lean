import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- AND gate: outputs 1 only when both inputs are 1. -/
def prob090_circuit1 {dom : DomainConfig}
    (a b : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  a &&& b

#synthesizeVerilog prob090_circuit1
