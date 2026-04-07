import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Always outputs logic high (1). -/
def prob003_step_one {dom : DomainConfig}
    : Signal dom (BitVec 1) :=
  Signal.pure 1#1

#synthesizeVerilog prob003_step_one