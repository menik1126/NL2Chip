import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- gshare branch predictor with 7-bit PC, 7-bit history, 128-entry PHT
    
    KNOWN ISSUE: This implementation is incomplete due to a Sparkle compiler bug.
    The compiler fails with "Unbound variable" error when Signal.memory or
    Signal.memoryComboRead is used before tuple construction (bundle2 or <$>/<*>).
    
    This prevents implementing the PHT (Pattern History Table) correctly.
    The current implementation only maintains the history register without
    the PHT-based prediction logic.
    
    To reproduce the bug, try adding:
      let pht_read := Signal.memoryComboRead addr data we raddr
    before the bundle2 call - it will fail to compile.
-/
def prob153_gshare {dom : DomainConfig}
    (predict_valid : Signal dom Bool)
    (predict_pc : Signal dom (BitVec 7))
    (train_valid : Signal dom Bool)
    (train_taken : Signal dom Bool)
    (train_mispredicted : Signal dom Bool)
    (train_history : Signal dom (BitVec 7))
    (train_pc : Signal dom (BitVec 7))
    : Signal dom (BitVec 1 × BitVec 7) :=
  
  -- History register with feedback
  let history : Signal dom (BitVec 7) := Signal.loop fun history =>
    let train_taken_bv : Signal dom (BitVec 1) := 
      Signal.mux train_taken (Signal.pure 1#1) (Signal.pure 0#1)
    let train_taken_ext := Signal.map (fun tt => tt.zeroExtend 7) train_taken_bv
    let history_after_train := (train_history <<< 1#7) ||| train_taken_ext
    
    -- For prediction, shift history and insert predict_taken
    -- TODO: predict_taken should come from PHT, but we can't compute it due to compiler bug
    let predict_taken_placeholder := Signal.pure 0#1
    let predict_taken_ext := Signal.map (fun pt => pt.zeroExtend 7) predict_taken_placeholder
    let history_after_predict := (history <<< 1#7) ||| predict_taken_ext
    
    let history_next : Signal dom (BitVec 7) :=
      Signal.mux train_mispredicted
        history_after_train
        (Signal.mux predict_valid history_after_predict history)
    
    Signal.register 0#7 history_next
  
  -- TODO: PHT logic should go here, but compiler bug prevents it
  -- The PHT should:
  -- 1. Read current value at train_index for read-modify-write
  -- 2. Increment/decrement based on train_taken (saturating 2-bit counter)
  -- 3. Write back to PHT
  -- 4. Read PHT at predict_index for prediction
  -- 5. Extract bit [1] as predict_taken
  
  -- Output: (predict_taken, predict_history)
  -- Using placeholder 0 for predict_taken due to compiler limitation
  bundle2 (Signal.pure 0#1) history

#synthesizeVerilog prob153_gshare
