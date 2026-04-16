import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding: OFF = 0, ON = 1
private abbrev stOFF : BitVec 1 := 0#1
private abbrev stON  : BitVec 1 := 1#1

/-- Moore FSM with two states (OFF=0, ON=1).
    OFF: out=0, j=0→OFF, j=1→ON.
    ON:  out=1, k=0→ON,  k=1→OFF.
    Active-high asynchronous reset to state OFF (modeled as sync mux). -/
def prob110_fsm2 {dom : DomainConfig}
    (areset : Signal dom Bool)
    (j : Signal dom Bool)
    (k : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  Signal.loop fun (state : Signal dom (BitVec 1)) =>
    -- isON = (state == ON)
    let isON : Signal dom Bool := state === (Signal.pure stON)
    -- Next state logic:
    --   When OFF: next = j ? ON : OFF
    --   When ON:  next = k ? OFF : ON
    let nextWhenOFF := Signal.mux j (Signal.pure stON) (Signal.pure stOFF)
    let nextWhenON  := Signal.mux k (Signal.pure stOFF) (Signal.pure stON)
    let nextState   := Signal.mux isON nextWhenON nextWhenOFF
    -- Apply async reset (modeled as sync): areset → OFF
    let nextWithReset := Signal.mux areset (Signal.pure stOFF) nextState
    -- Register with initial value OFF
    Signal.register stOFF nextWithReset

#synthesizeVerilog prob110_fsm2
