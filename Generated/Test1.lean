import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test -/
def test1 {dom : DomainConfig}
    (x : Signal dom Bool) (y : Signal dom (BitVec 3))
    : Signal dom (BitVec 1 × BitVec 1) :=
  let Y0 : Signal dom (BitVec 1) := Signal.pure 0#1
  let z : Signal dom (BitVec 1) := Signal.pure 1#1
  bundle2 Y0 z

#synthesize test1
