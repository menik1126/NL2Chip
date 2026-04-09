import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding
private abbrev stA : BitVec 1 := 0#1
private abbrev stB : BitVec 1 := 1#1

/-- Moore FSM: 2 states (A=0, B=1), output is 1 when in state B.
    Synchronous reset to state B. -/
def prob107_fsm1s {dom : DomainConfig}
    (reset : Signal dom Bool)
    (inp : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  Signal.loop fun (state : Signal dom (BitVec 1)) =>
    -- Next state logic:
    --   B + in=0 → A,  B + in=1 → B
    --   A + in=0 → B,  A + in=1 → A
    -- Observation: when in=1, state stays; when in=0, state flips.
    -- next = in ? state : ~state
    let stateFlipped := ~~~state
    let nextState := Signal.mux inp state stateFlipped
    -- Apply synchronous reset: reset → B
    let nextWithReset := Signal.mux reset (Signal.pure stB) nextState
    -- Register with initial value B (matches reset behavior)
    Signal.register stB nextWithReset

#synthesizeVerilog prob107_fsm1s