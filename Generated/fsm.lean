import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding for sequence detector FSM
private abbrev s0 : BitVec 3 := 0#3  -- idle
private abbrev s1 : BitVec 3 := 1#3  -- saw 1
private abbrev s2 : BitVec 3 := 2#3  -- saw 10
private abbrev s3 : BitVec 3 := 3#3  -- saw 100
private abbrev s4 : BitVec 3 := 4#3  -- saw 1001
private abbrev s5 : BitVec 3 := 5#3  -- saw 10011

/-- Mealy FSM that detects the sequence 10011.
    MATCH output is 1 when the sequence completes (state s4 and IN=1). -/
def fsm {dom : DomainConfig}
    (rst : Signal dom Bool)
    (inp : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  let state := Signal.loop fun (state : Signal dom (BitVec 3)) =>
    -- Next state logic based on current state and input
    let nextState := 
      hw_cond (Signal.pure s0)
        | (state === Signal.pure s0) &&& (~~~inp) => Signal.pure s0
        | (state === Signal.pure s0) &&& inp => Signal.pure s1
        | (state === Signal.pure s1) &&& (~~~inp) => Signal.pure s2
        | (state === Signal.pure s1) &&& inp => Signal.pure s1
        | (state === Signal.pure s2) &&& (~~~inp) => Signal.pure s3
        | (state === Signal.pure s2) &&& inp => Signal.pure s1
        | (state === Signal.pure s3) &&& (~~~inp) => Signal.pure s0
        | (state === Signal.pure s3) &&& inp => Signal.pure s4
        | (state === Signal.pure s4) &&& (~~~inp) => Signal.pure s2
        | (state === Signal.pure s4) &&& inp => Signal.pure s5
        | (state === Signal.pure s5) &&& (~~~inp) => Signal.pure s2
        | (state === Signal.pure s5) &&& inp => Signal.pure s1
    
    -- Apply reset
    let nextWithReset := Signal.mux rst (Signal.pure s0) nextState
    
    -- Register the state
    Signal.register s0 nextWithReset
  
  -- Mealy output: MATCH = 1 when state=s4 and inp=1
  let isS4 := state === Signal.pure s4
  let matchOutput := isS4 &&& inp
  Signal.mux matchOutput (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog fsm
