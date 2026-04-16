import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_fsm {dom : DomainConfig}
    (inp : Signal dom Bool)
    (state : Signal dom (BitVec 10))
    : Signal dom (BitVec 1 × BitVec 1) :=
  let s7 : Signal dom Bool := Signal.map (fun x => x.getLsb 7) state
  let s8 : Signal dom Bool := Signal.map (fun x => x.getLsb 8) state
  let s9 : Signal dom Bool := Signal.map (fun x => x.getLsb 9) state
  let out1 : Signal dom (BitVec 1) := Signal.mux (s8 ||| s9) (Signal.pure 1#1) (Signal.pure 0#1)
  let out2 : Signal dom (BitVec 1) := Signal.mux (s7 ||| s9) (Signal.pure 1#1) (Signal.pure 0#1)
  bundle2 out1 out2

#synthesizeVerilog test_fsm
