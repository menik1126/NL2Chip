import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 32-bit global history shift register with misprediction rollback support.
    Shifts in predict_taken when predict_valid=1, or loads train_history concatenated
    with train_taken when train_mispredicted=1 (priority). Async reset to 0. -/
def prob118_history_shift {dom : DomainConfig}
    (areset : Signal dom Bool)
    (predict_valid : Signal dom Bool)
    (predict_taken : Signal dom Bool)
    (train_mispredicted : Signal dom Bool)
    (train_taken : Signal dom Bool)
    (train_history : Signal dom (BitVec 32))
    : Signal dom (BitVec 32) :=
  Signal.loop fun (history : Signal dom (BitVec 32)) =>
    -- Convert Bool to BitVec 1
    let predict_taken_bv := Signal.mux predict_taken (Signal.pure 1#1) (Signal.pure 0#1)
    let train_taken_bv := Signal.mux train_taken (Signal.pure 1#1) (Signal.pure 0#1)
    
    -- For predict: {history[30:0], predict_taken} = (history << 1) | predict_taken
    let predict_shift := history <<< 1#32
    let predict_taken_ext := Signal.map (fun b => BitVec.zeroExtend 32 b) predict_taken_bv
    let predict_next := predict_shift ||| predict_taken_ext
    
    -- For train: {train_history[30:0], train_taken} = (train_history << 1) | train_taken
    let train_shift := train_history <<< 1#32
    let train_taken_ext := Signal.map (fun b => BitVec.zeroExtend 32 b) train_taken_bv
    let train_next := train_shift ||| train_taken_ext
    
    -- Priority mux: train_mispredicted > predict_valid > hold
    let next_val := Signal.mux train_mispredicted 
      train_next
      (Signal.mux predict_valid predict_next history)
    
    -- Apply async reset (modeled as sync mux)
    let next_with_reset := Signal.mux areset (Signal.pure 0#32) next_val
    
    Signal.register 0#32 next_with_reset

#synthesizeVerilog prob118_history_shift
