import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding
private abbrev stOFF : BitVec 1 := 0#1
private abbrev stON : BitVec 1 := 1#1

/-- Moore FSM: 2 states (OFF=0, ON=1), inputs j and k.
    OFF + j=1 → ON, OFF + j=0 → OFF
    ON + k=1 → OFF, ON + k=0 → ON
    Async reset to OFF. -/
def prob110_fsm2 {dom : DomainConfig}
    (areset : Signal dom Bool)
    (j : Signal dom Bool)
    (k : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  Signal.loop fun (state : Signal dom (BitVec 1)) =>
    -- Check current state
    let isOFF := state === Signal.pure stOFF
    
    -- Next state logic:
    -- If in OFF: j=1 → ON, j=0 → OFF
    -- If in ON:  k=1 → OFF, k=0 → ON
    let nextFromOFF := Signal.mux j (Signal.pure stON) (Signal.pure stOFF)
    let nextFromON := Signal.mux k (Signal.pure stOFF) (Signal.pure stON)
    let nextState := Signal.mux isOFF nextFromOFF nextFromON
    
    -- Apply async reset (modeled as sync): areset → OFF
    let nextWithReset := Signal.mux areset (Signal.pure stOFF) nextState
    
    -- Register with initial value OFF (matches areset behavior)
    Signal.register stOFF nextWithReset

#synthesizeVerilog prob110_fsm2
