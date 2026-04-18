import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding (need 3 bits for 5 states)
private abbrev stS    : BitVec 3 := 0#3  -- Initial state
private abbrev stS1   : BitVec 3 := 1#3  -- Saw 1
private abbrev stS11  : BitVec 3 := 2#3  -- Saw 11
private abbrev stS110 : BitVec 3 := 3#3  -- Saw 110
private abbrev stDone : BitVec 3 := 4#3  -- Saw 1101 (final)

/-- FSM that searches for sequence 1101 in input bit stream.
    When found, sets start_shifting to 1 forever until reset. -/
def prob096_review2015_fsmseq {dom : DomainConfig}
    (reset : Signal dom Bool)
    (data : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  let state := Signal.loop fun (state : Signal dom (BitVec 3)) =>
    -- Next state logic based on current state and data input
    let isS    := state === Signal.pure stS
    let isS1   := state === Signal.pure stS1
    let isS11  := state === Signal.pure stS11
    let isS110 := state === Signal.pure stS110
    
    -- State transitions:
    -- S:    data=1 → S1,   data=0 → S
    -- S1:   data=1 → S11,  data=0 → S
    -- S11:  data=1 → S11,  data=0 → S110
    -- S110: data=1 → Done, data=0 → S
    -- Done: stay in Done
    
    let nextFromS    := Signal.mux data (Signal.pure stS1) (Signal.pure stS)
    let nextFromS1   := Signal.mux data (Signal.pure stS11) (Signal.pure stS)
    let nextFromS11  := Signal.mux data (Signal.pure stS11) (Signal.pure stS110)
    let nextFromS110 := Signal.mux data (Signal.pure stDone) (Signal.pure stS)
    let nextFromDone := Signal.pure stDone
    
    -- Priority mux to select next state
    let next1 := Signal.mux isS nextFromS nextFromDone
    let next2 := Signal.mux isS1 nextFromS1 next1
    let next3 := Signal.mux isS11 nextFromS11 next2
    let next4 := Signal.mux isS110 nextFromS110 next3
    
    -- Apply reset (synchronous, active high)
    let nextWithReset := Signal.mux reset (Signal.pure stS) next4
    
    -- Register the state
    Signal.register stS nextWithReset
  
  -- Output: start_shifting = 1 when state == Done
  Signal.mux (state === Signal.pure stDone) (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob096_review2015_fsmseq
