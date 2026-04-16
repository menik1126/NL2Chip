import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test nested bundle2 -/
def test_bundle2 {dom : DomainConfig}
    (a : Signal dom (BitVec 10))
    (b : Signal dom (BitVec 1))
    : Signal dom (BitVec 10 × (BitVec 1 × BitVec 1)) :=
  let c := Signal.pure 0#1
  bundle2 a (bundle2 b c)

#synthesizeVerilog test_bundle2
