import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding
private abbrev stA : BitVec 1 := 0#1
private abbrev stB : BitVec 1 := 1#1

/-- Moore FSM: 2 states (A=0, B=1), output is 1 when in state B.
    Async reset to state B. -/
def prob109_fsm1 {dom : DomainConfig}
    (areset : Signal dom Bool)
    (inp : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  Signal.loop fun (state : Signal dom (BitVec 1)) =>
    -- Next state logic based on the state diagram:
    --   A (0) + in=0 → B (1)
    --   A (0) + in=1 → A (0)  
    --   B (1) + in=0 → A (0)
    --   B (1) + in=1 → B (1)
    -- Pattern: when in=1, state stays same; when in=0, state flips
    let stateFlipped := ~~~state
    let nextState := Signal.mux inp state stateFlipped
    -- Apply async reset (modeled as sync mux): areset → B
    let nextWithReset := Signal.mux areset (Signal.pure stB) nextState
    -- Register with initial value B (matches areset behavior)
    Signal.register stB nextWithReset

#synthesizeVerilog prob109_fsm1