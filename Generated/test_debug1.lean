import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test 1: Just Signal.map + bundle2
def test1 {dom : DomainConfig}
    (state : Signal dom (BitVec 4)) (inp : Signal dom Bool)
    : Signal dom (BitVec 4 × BitVec 1) :=
  let sA : Signal dom Bool := Signal.map (fun x => x.getLsb 0) state
  let out : Signal dom (BitVec 1) := Signal.mux sA (Signal.pure 1#1) (Signal.pure 0#1)
  bundle2 state out

#synthesizeVerilog test1
