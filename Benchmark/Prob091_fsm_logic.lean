/-
  VerilogEval Prob091: FSM state output logic

  NL Description:
  FSM state output logic for one-hot encoded FSM.
-/

import cktlean
import cktlean.Compiler.Elab

open cktlean.Core.Domain
open cktlean.Core.Signal

/-- FSM state output logic: computes Y1 and Y3 based on current state and input -/
def prob091_fsm_logic {dom : DomainConfig}
    (y : Signal dom (BitVec 6)) (w : Signal dom Bool)
    : Signal dom (BitVec 1 × BitVec 1) :=
  -- Extract individual state bits as Bool signals
  let y0 : Signal dom Bool := Signal.map (fun x => x.getLsb 0) y  -- State A
  let y1 : Signal dom Bool := Signal.map (fun x => x.getLsb 1) y  -- State B  
  let y2 : Signal dom Bool := Signal.map (fun x => x.getLsb 2) y  -- State C
  let y4 : Signal dom Bool := Signal.map (fun x => x.getLsb 4) y  -- State E
  let y5 : Signal dom Bool := Signal.map (fun x => x.getLsb 5) y  -- State F
  
  -- Y1 = y[0] & w  (A with input 1 → B)
  let Y1_bool : Signal dom Bool := y0 &&& w
  let Y1 : Signal dom (BitVec 1) := Signal.mux Y1_bool (Signal.pure 1#1) (Signal.pure 0#1)
  
  -- Y3 = (y[1] | y[2] | y[4] | y[5]) & ~w  (B,C,E,F with input 0 → D)  
  let states_to_D : Signal dom Bool := y1 ||| y2 ||| y4 ||| y5
  let not_w : Signal dom Bool := ~~~w
  let Y3_bool : Signal dom Bool := states_to_D &&& not_w
  let Y3 : Signal dom (BitVec 1) := Signal.mux Y3_bool (Signal.pure 1#1) (Signal.pure 0#1)
  
  bundle2 Y1 Y3

#synthesizeVerilog prob091_fsm_logic