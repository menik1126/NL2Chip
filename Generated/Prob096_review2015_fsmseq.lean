import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding: 3-bit states
private abbrev stS    : BitVec 3 := 0#3  -- initial state
private abbrev stS1   : BitVec 3 := 1#3  -- seen "1"
private abbrev stS11  : BitVec 3 := 2#3  -- seen "11"
private abbrev stS110 : BitVec 3 := 3#3  -- seen "110"
private abbrev stDone : BitVec 3 := 4#3  -- seen "1101", start_shifting forever

/-- FSM that detects sequence 1101 in a bit stream.
    When the sequence is found, start_shifting is set to 1 forever (until reset).
    Reset is active high synchronous. -/
def prob096_review2015_fsmseq {dom : DomainConfig}
    (reset : Signal dom Bool)
    (data  : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  -- Use Signal.loop to get the registered state, then derive output
  let state : Signal dom (BitVec 3) :=
    Signal.loop fun (state : Signal dom (BitVec 3)) =>
      -- Compute next state based on current state and data
      let isS    := state === (Signal.pure stS)
      let isS1   := state === (Signal.pure stS1)
      let isS11  := state === (Signal.pure stS11)
      let isS110 := state === (Signal.pure stS110)
      let isDone := state === (Signal.pure stDone)
      -- S:    data=1 → S1,   data=0 → S
      let nextFromS    := Signal.mux data (Signal.pure stS1) (Signal.pure stS)
      -- S1:   data=1 → S11,  data=0 → S
      let nextFromS1   := Signal.mux data (Signal.pure stS11) (Signal.pure stS)
      -- S11:  data=1 → S11,  data=0 → S110
      let nextFromS11  := Signal.mux data (Signal.pure stS11) (Signal.pure stS110)
      -- S110: data=1 → Done, data=0 → S
      let nextFromS110 := Signal.mux data (Signal.pure stDone) (Signal.pure stS)
      -- Done: stay Done
      let nextFromDone := Signal.pure stDone
      -- Priority mux to select next state
      let nextState :=
        hw_cond (Signal.pure stS)
        | isS    => nextFromS
        | isS1   => nextFromS1
        | isS11  => nextFromS11
        | isS110 => nextFromS110
        | isDone => nextFromDone
      -- Apply synchronous reset
      let nextWithReset := Signal.mux reset (Signal.pure stS) nextState
      -- Register the state
      Signal.register stS nextWithReset
  -- Output: start_shifting = (state == Done)
  let isDone := state === (Signal.pure stDone)
  Signal.mux isDone (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob096_review2015_fsmseq
