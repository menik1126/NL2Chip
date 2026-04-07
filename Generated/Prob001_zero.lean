import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Module that always outputs a LOW (0) signal. -/
def prob001_zero {dom : DomainConfig}
    : Signal dom (BitVec 1) :=
  Signal.pure 0#1

#synthesizeVerilog prob001_zero