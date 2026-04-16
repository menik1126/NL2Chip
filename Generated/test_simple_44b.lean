import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_simple_44b {dom : DomainConfig}
    (a b : Signal dom (BitVec 3))
    : Signal dom (BitVec 3 × BitVec 1) :=
  let or_result : Signal dom (BitVec 3) := a ||| b
  let bit0 : Signal dom Bool := Signal.map (fun v => v.getLsb 0) or_result
  let bit1 : Signal dom Bool := Signal.map (fun v => v.getLsb 1) or_result
  let bit2 : Signal dom Bool := Signal.map (fun v => v.getLsb 2) or_result
  let logical_or_bool : Signal dom Bool := bit0 ||| bit1 ||| bit2
  let out_or_logical : Signal dom (BitVec 1) :=
    Signal.mux logical_or_bool (Signal.pure 1#1) (Signal.pure 0#1)
  bundle2 a out_or_logical

#synthesizeVerilog test_simple_44b
