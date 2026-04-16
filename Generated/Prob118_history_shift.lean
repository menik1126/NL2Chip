import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 32-bit global history shift register with misprediction rollback.
    - On areset: register resets to 0
    - On train_mispredicted: load {train_history[30:0], train_taken}
    - On predict_valid (if no misprediction): shift in predict_taken from LSB
    - predict_history is the current register value -/
def prob118_history_shift {dom : DomainConfig}
    (areset          : Signal dom Bool)
    (predict_valid   : Signal dom Bool)
    (predict_taken   : Signal dom Bool)
    (train_mispredicted : Signal dom Bool)
    (train_taken     : Signal dom Bool)
    (train_history   : Signal dom (BitVec 32))
    : Signal dom (BitVec 32) :=
  -- Convert Bool signals to BitVec 1 for concatenation using mux
  let predict_taken_bv : Signal dom (BitVec 1) := Signal.mux predict_taken (Signal.pure 1#1) (Signal.pure 0#1)
  let train_taken_bv   : Signal dom (BitVec 1) := Signal.mux train_taken (Signal.pure 1#1) (Signal.pure 0#1)
  -- Build the shift register with feedback
  Signal.loop fun (hist : Signal dom (BitVec 32)) =>
    -- {train_history[30:0], train_taken}: shift in train_taken as LSB, drop MSB of train_history
    let train_hist31 : Signal dom (BitVec 31) := Signal.map (fun v => BitVec.extractLsb' 0 31 v) train_history
    let mispred_val  : Signal dom (BitVec 32) := train_hist31 ++ train_taken_bv
    -- {predict_history[30:0], predict_taken}: shift in predict_taken as LSB, drop MSB
    let hist31 : Signal dom (BitVec 31) := Signal.map (fun v => BitVec.extractLsb' 0 31 v) hist
    let pred_val : Signal dom (BitVec 32) := hist31 ++ predict_taken_bv
    -- Priority: misprediction > prediction > hold
    let next := Signal.mux train_mispredicted mispred_val
                  (Signal.mux predict_valid pred_val hist)
    -- Async reset to 0 modeled as synchronous mux (areset takes highest priority)
    let next_with_reset := Signal.mux areset (Signal.pure 0#32) next
    Signal.register 0#32 next_with_reset

#synthesizeVerilog prob118_history_shift
