import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test: Signal.mux with Bool condition, then bundle2
def test3 {dom : DomainConfig}
    (state : Signal dom (BitVec 4)) (sA : Signal dom Bool)
    : Signal dom (BitVec 4 × BitVec 1) :=
  let out : Signal dom (BitVec 1) := Signal.mux sA (Signal.pure 1#1) (Signal.pure 0#1)
  bundle2 state out

#synthesizeVerilog test3
