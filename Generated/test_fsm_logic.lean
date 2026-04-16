import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- FSM state output logic: test version -/
def test_fsm_logic {dom : DomainConfig}
    (y : Signal dom (BitVec 6)) (w : Signal dom Bool)
    : Signal dom (BitVec 1 × BitVec 1) :=
  let Y1 := Signal.pure 0#1
  let Y3 := Signal.pure 0#1  
  bundle2 Y1 Y3

#synthesizeVerilog test_fsm_logic