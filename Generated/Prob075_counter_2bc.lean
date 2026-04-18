import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Two-bit saturating counter for branch prediction.
    Increments (max 3) when train_valid=1 and train_taken=1.
    Decrements (min 0) when train_valid=1 and train_taken=0.
    Async reset to weakly not-taken (2'b01 = 1). -/
def prob075_counter_2bc {dom : DomainConfig}
    (areset : Signal dom Bool)
    (train_valid : Signal dom Bool)
    (train_taken : Signal dom Bool)
    : Signal dom (BitVec 2) :=
  Signal.loop fun (state : Signal dom (BitVec 2)) =>
    -- Check boundaries
    let atMax := state === 3#2  -- state == 3
    let atMin := state === 0#2  -- state == 0
    
    -- Determine next state based on training
    let shouldIncrement := train_valid &&& train_taken &&& (~~~atMax)
    let shouldDecrement := train_valid &&& (~~~train_taken) &&& (~~~atMin)
    
    let incremented := state + 1#2
    let decremented := state - 1#2
    
    -- Priority: increment, then decrement, else keep
    let nextState := Signal.mux shouldIncrement incremented
                      (Signal.mux shouldDecrement decremented state)
    
    -- Apply async reset (modeled as sync): areset → 1
    let nextWithReset := Signal.mux areset (Signal.pure 1#2) nextState
    
    -- Register with initial value 1 (weakly not-taken)
    Signal.register 1#2 nextWithReset

#synthesizeVerilog prob075_counter_2bc
