import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Wire: passes a 1-bit input signal directly to the output. -/
def prob007_wire {dom : DomainConfig}
    (in_ : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  in_

#synthesizeVerilog prob007_wire
