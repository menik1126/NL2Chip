import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Wire: passes a 1-bit input signal through unchanged. -/
def prob007_wire {dom : DomainConfig}
    (input : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  input

#synthesizeVerilog prob007_wire