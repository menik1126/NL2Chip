import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test134 {dom : DomainConfig}
    (a b : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1) :=
  let sum  := a ^^^ b
  let cout := a &&& b
  bundle2 sum cout

#synthesizeVerilog test134
