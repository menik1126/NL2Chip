import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- NOR gate: outputs 1 only when both inputs are 0. -/
def prob011_norgate {dom : DomainConfig}
    (a b : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  ~~~(a ||| b)

#synthesizeVerilog prob011_norgate
