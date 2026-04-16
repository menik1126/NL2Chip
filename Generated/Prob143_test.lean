import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- FSM one-hot state transition and output logic -/
def prob143_fsm_onehot {dom : DomainConfig}
    (inp : Signal dom Bool)
    (state : Signal dom (BitVec 10))
    : Signal dom (BitVec 12) :=
  let ns := Signal.pure 0#10
  let o1 := Signal.pure 0#1
  let o2 := Signal.pure 0#1
  Signal.map (fun x => x.2.2 ++ x.2.1 ++ x.1) (bundle2 ns (bundle2 o1 o2))

#synthesizeVerilog prob143_fsm_onehot
