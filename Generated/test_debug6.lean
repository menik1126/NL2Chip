import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test: just signal.map with getLsb returning Bool, then bundle2 without using it
def test6 {dom : DomainConfig}
    (state : Signal dom (BitVec 4))
    : Signal dom (BitVec 4 × BitVec 1) :=
  let _sA : Signal dom Bool := Signal.map (fun x => x.getLsb 0) state
  bundle2 (Signal.pure 0#4) (Signal.pure 0#1)

#synthesizeVerilog test6
