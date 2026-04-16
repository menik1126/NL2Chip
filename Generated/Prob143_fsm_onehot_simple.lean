import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- FSM one-hot state transition and output logic. Given current state (10 bits one-hot)
    and input, computes next_state (10 bits) and two outputs. -/
def prob143_fsm_onehot {dom : DomainConfig}
    (inp : Signal dom Bool)
    (state : Signal dom (BitVec 10))
    : Signal dom (BitVec 10 × (BitVec 1 × BitVec 1)) :=
  let next_state := Signal.pure 0#10
  let out1 := Signal.pure 0#1
  let out2 := Signal.pure 0#1
  bundle2 next_state (bundle2 out1 out2)

#synthesizeVerilog prob143_fsm_onehot
