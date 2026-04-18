import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test -/
def test4 {dom : DomainConfig}
    (a b : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1) :=
  let sum  := Signal.pure 0#1
  let cout := Signal.pure 1#1
  bundle2 sum cout

#synthesizeVerilog test4
