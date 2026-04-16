import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding: OFF = 0, ON = 1
private abbrev stOFF : BitVec 1 := 0#1
private abbrev stON  : BitVec 1 := 1#1

/-- Moore FSM with two states (OFF=0, ON=1), synchronous reset to OFF.
    OFF: out=0, j=1 → ON, j=0 → OFF
    ON:  out=1, k=1 → OFF, k=0 → ON -/
def prob111_fsm2s {dom : DomainConfig}
    (reset : Signal dom Bool)
    (j : Signal dom Bool)
    (k : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  Signal.loop fun (state : Signal dom (BitVec 1)) =>
    -- Is the FSM in the ON state?
    let isON := state === (Signal.pure stON)
    -- Next state logic:
    --   OFF + j=0 → OFF, OFF + j=1 → ON
    --   ON  + k=0 → ON,  ON  + k=1 → OFF
    let nextFromOFF := Signal.mux j (Signal.pure stON) (Signal.pure stOFF)
    let nextFromON  := Signal.mux k (Signal.pure stOFF) (Signal.pure stON)
    let nextState   := Signal.mux isON nextFromON nextFromOFF
    -- Apply synchronous reset: reset → OFF
    let nextWithReset := Signal.mux reset (Signal.pure stOFF) nextState
    -- Register with initial value OFF
    Signal.register stOFF nextWithReset

#synthesizeVerilog prob111_fsm2s
