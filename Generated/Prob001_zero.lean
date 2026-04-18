import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Always outputs LOW (0). -/
def prob001_zero {dom : DomainConfig}
    : Signal dom (BitVec 1) :=
  Signal.pure 0#1

#synthesizeVerilog prob001_zero
