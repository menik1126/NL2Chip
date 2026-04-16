import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Always outputs logic low (0). -/
def prob002_m2014_q4i {dom : DomainConfig}
    : Signal dom (BitVec 1) :=
  Signal.pure 0#1

#synthesizeVerilog prob002_m2014_q4i
