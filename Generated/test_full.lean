import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test: Full construction from Bool to BitVec 4 and bundle -/
def test_full {dom : DomainConfig}
    (a b c d : Signal dom Bool)
    : Signal dom (BitVec 4 × BitVec 1) :=
  let a1 := Signal.mux a (Signal.pure 1#1) (Signal.pure 0#1)
  let b1 := Signal.mux b (Signal.pure 1#1) (Signal.pure 0#1)
  let c1 := Signal.mux c (Signal.pure 1#1) (Signal.pure 0#1)
  let d1 := Signal.mux d (Signal.pure 1#1) (Signal.pure 0#1)
  let lo := b1 ++ a1
  let hi := d1 ++ c1
  let result4 := hi ++ lo
  let out := Signal.mux d (Signal.pure 1#1) (Signal.pure 0#1)
  bundle2 result4 out

#synthesizeVerilog test_full
