import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def prob091_test2 {dom : DomainConfig}
    (y : Signal dom (BitVec 6)) (w : Signal dom Bool)
    : Signal dom (BitVec 1 × BitVec 1) :=
  let y0 : Signal dom Bool := Signal.map (fun x => x.getLsb 0) y
  let y1 : Signal dom Bool := Signal.map (fun x => x.getLsb 1) y
  let Y1_bool : Signal dom Bool := y0 &&& w
  let Y1 : Signal dom (BitVec 1) := Signal.mux Y1_bool (Signal.pure 1#1) (Signal.pure 0#1)
  let states_to_D : Signal dom Bool := y1
  let Y3_bool : Signal dom Bool := states_to_D
  let Y3 : Signal dom (BitVec 1) := Signal.mux Y3_bool (Signal.pure 1#1) (Signal.pure 0#1)
  bundle2 Y1 Y3

#synthesizeVerilog prob091_test2
