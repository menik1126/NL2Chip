import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Two-bit saturating counter with async reset to weakly not-taken (2'b01).
    Increments (up to 3) when train_valid=1 and train_taken=1.
    Decrements (down to 0) when train_valid=1 and train_taken=0.
    Holds value when train_valid=0. -/
def prob075_counter_2bc {dom : DomainConfig}
    (areset : Signal dom Bool)
    (train_valid : Signal dom Bool)
    (train_taken : Signal dom Bool)
    : Signal dom (BitVec 2) :=
  Signal.loop fun (state : Signal dom (BitVec 2)) =>
    -- Check saturation limits
    let atMax := state === (Signal.pure 3#2)   -- state == 3, cannot increment
    let atMin := state === (Signal.pure 0#2)   -- state == 0, cannot decrement
    -- Compute next value when training
    let incremented := state + 1#2
    let decremented := state - 1#2
    -- When train_taken=1: increment if not at max, else hold
    -- When train_taken=0: decrement if not at min, else hold
    let nextWhenTaken    := Signal.mux atMax state incremented
    let nextWhenNotTaken := Signal.mux atMin state decremented
    -- Select between increment/decrement based on train_taken
    let nextWhenTraining := Signal.mux train_taken nextWhenTaken nextWhenNotTaken
    -- If not training, hold; if training, update
    let nextNormal := Signal.mux train_valid nextWhenTraining state
    -- Apply async reset (modeled as sync mux): areset → 2'b01
    let next := Signal.mux areset (Signal.pure 1#2) nextNormal
    Signal.register 1#2 next

#synthesizeVerilog prob075_counter_2bc
