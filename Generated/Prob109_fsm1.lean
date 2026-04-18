import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding
private abbrev stA : BitVec 1 := 0#1
private abbrev stB : BitVec 1 := 1#1

/-- Moore FSM: 2 states (A=0, B=1), output is 1 when in state B.
    Async reset to state B modeled as synchronous mux. -/
def prob109_fsm1 {dom : DomainConfig}
    (areset : Signal dom Bool)
    (inp : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  Signal.loop fun (state : Signal dom (BitVec 1)) =>
    -- Next state logic:
    --   A + in=0 → B,  A + in=1 → A
    --   B + in=0 → A,  B + in=1 → B
    -- Observation: when in=1, state stays; when in=0, state flips.
    -- next = in ? state : ~state
    let stateFlipped := ~~~state
    let nextState := Signal.mux inp state stateFlipped
    -- Apply async reset (modeled as sync): areset → B
    let nextWithReset := Signal.mux areset (Signal.pure stB) nextState
    -- Register with initial value B (matches areset behavior)
    let regState := Signal.register stB nextWithReset
    -- Output: state == B (i.e., state itself since B=1)
    -- But we need to return the registered state for the loop
    regState

#synthesizeVerilog prob109_fsm1
