import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- NOR gate: output is the bitwise NOT of (a OR b). -/
def prob011_norgate {dom : DomainConfig}
    (a b : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  ~~~(a ||| b)

#synthesizeVerilog prob011_norgate
