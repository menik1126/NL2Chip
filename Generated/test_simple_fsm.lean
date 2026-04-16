import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Simple test with two outputs -/
def test_simple_fsm {dom : DomainConfig}
    (reset : Signal dom Bool)
    (data : Signal dom Bool)
    : Signal dom (BitVec 1 × BitVec 1) :=
  let state : Signal dom (BitVec 2) :=
    Signal.loop fun (state : Signal dom (BitVec 2)) =>
      let nextState := Signal.mux data (state + 1#2) state
      let nextWithReset := Signal.mux reset (Signal.pure 0#2) nextState
      Signal.register 0#2 nextWithReset
  
  let out1 := Signal.mux (state === Signal.pure 0#2) (Signal.pure 1#1) (Signal.pure 0#1)
  let out2 := Signal.mux (state === Signal.pure 1#2) (Signal.pure 1#1) (Signal.pure 0#1)
  
  bundle2 out1 out2

#synthesizeVerilog test_simple_fsm