import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Two-bit saturating counter with async reset to 1. Increments on train_valid=1 & train_taken=1 (saturates at 3), decrements on train_valid=1 & train_taken=0 (saturates at 0). -/
def prob075_counter_2bc {dom : DomainConfig}
    (areset : Signal dom Bool)
    (train_valid : Signal dom Bool)
    (train_taken : Signal dom Bool)
    : Signal dom (BitVec 2) :=
  Signal.loop fun (state : Signal dom (BitVec 2)) =>
    -- Training conditions
    let shouldIncrement := train_valid &&& train_taken
    let shouldDecrement := train_valid &&& (~~~train_taken)
    
    -- Saturation checks
    let atMax := state === 3#2  -- state == 3
    let atMin := state === 0#2  -- state == 0
    
    -- Conditional increment/decrement with saturation
    let canIncrement := shouldIncrement &&& (~~~atMax)
    let canDecrement := shouldDecrement &&& (~~~atMin)
    
    -- Next state calculation
    let nextState := hw_cond state
      | canIncrement => state + 1#2
      | canDecrement => state - 1#2
    
    -- Apply async reset (modeled as sync mux): areset → 1
    let nextWithReset := Signal.mux areset (Signal.pure 1#2) nextState
    
    -- Register with initial value 1 (matches areset behavior)
    Signal.register 1#2 nextWithReset

#synthesizeVerilog prob075_counter_2bc