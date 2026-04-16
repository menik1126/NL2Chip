import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test bundle3 -/
def test_bundle3 {dom : DomainConfig}
    (a b : Signal dom (BitVec 3))
    : Signal dom (BitVec 3 × BitVec 1 × BitVec 6) :=
  let out1 := a ||| b
  let out2 := Signal.pure 1#1
  let out3 := b ++ a
  bundle3 out1 out2 out3

#synthesizeVerilog test_bundle3
