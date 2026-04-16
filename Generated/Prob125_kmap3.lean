import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Karnaugh map implementation: out = a | (~b & c) -/
def prob125_kmap3 {dom : DomainConfig}
    (a b c d : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  a ||| ((~~~b) &&& c)

#synthesizeVerilog prob125_kmap3
