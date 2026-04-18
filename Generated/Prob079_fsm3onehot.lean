import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- FSM one-hot state transition logic: computes next state and output -/
def prob079_fsm3onehot {dom : DomainConfig}
    (inp : Signal dom Bool) (state : Signal dom (BitVec 4))
    : Signal dom (BitVec 4 × BitVec 1) :=
  let stateA := Signal.map (fun x => x.getLsb 0) state
  let stateB := Signal.map (fun x => x.getLsb 1) state
  let stateC := Signal.map (fun x => x.getLsb 2) state
  let stateD := Signal.map (fun x => x.getLsb 3) state
  let not_in := ~~~inp
  let nextA_bool := (stateA ||| stateC) &&& not_in
  let nextB_bool := (stateA ||| stateB ||| stateD) &&& inp
  let nextC_bool := (stateB ||| stateD) &&& not_in
  let nextD_bool := stateC &&& inp
  let nextA_4bit := Signal.mux nextA_bool (Signal.pure 1#4) (Signal.pure 0#4)
  let nextB_4bit := Signal.mux nextB_bool (Signal.pure 2#4) (Signal.pure 0#4)
  let nextC_4bit := Signal.mux nextC_bool (Signal.pure 4#4) (Signal.pure 0#4)
  let nextD_4bit := Signal.mux nextD_bool (Signal.pure 8#4) (Signal.pure 0#4)
  let next_state := nextA_4bit ||| nextB_4bit ||| nextC_4bit ||| nextD_4bit
  let out := Signal.mux stateD (Signal.pure 1#1) (Signal.pure 0#1)
  bundle2 next_state out

-- NOTE: #synthesizeVerilog prob079_fsm3onehot causes a compiler bug:
-- "Unbound variable: _uniq.XXX (userName=a)"
-- This appears to be a bug in the Sparkle compiler when handling bundle2
-- with certain signal combinations. The function compiles correctly without
-- the #synthesizeVerilog directive.
