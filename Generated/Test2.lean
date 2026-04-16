import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test -/
def test2 {dom : DomainConfig}
    (a : Signal dom Bool) (b : Signal dom Bool) (c : Signal dom Bool)
    : Signal dom (BitVec 1 × BitVec 1) :=
  let x := a &&& b &&& c
  let y := a ||| b
  let out1 := Signal.mux x (Signal.pure 1#1) (Signal.pure 0#1)
  let out2 := Signal.mux y (Signal.pure 1#1) (Signal.pure 0#1)
  bundle2 out1 out2

#synthesizeVerilog test2
