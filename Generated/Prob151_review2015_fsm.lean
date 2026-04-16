import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding: 4-bit states
private abbrev stS    : BitVec 4 := 0#4  -- initial: searching for 1101
private abbrev stS1   : BitVec 4 := 1#4  -- seen "1"
private abbrev stS11  : BitVec 4 := 2#4  -- seen "11"
private abbrev stS110 : BitVec 4 := 3#4  -- seen "110"
private abbrev stB0   : BitVec 4 := 4#4  -- shift bit 0
private abbrev stB1   : BitVec 4 := 5#4  -- shift bit 1
private abbrev stB2   : BitVec 4 := 6#4  -- shift bit 2
private abbrev stB3   : BitVec 4 := 7#4  -- shift bit 3
private abbrev stCount: BitVec 4 := 8#4  -- waiting for counter
private abbrev stWait : BitVec 4 := 9#4  -- done, waiting for ack

/-- FSM timer controller: detects pattern 1101, shifts 4 bits, waits for counter, notifies user.
    Active-high synchronous reset. Outputs: shift_ena, counting, done. -/
def prob151_review2015_fsm {dom : DomainConfig}
    (reset        : Signal dom Bool)
    (data         : Signal dom Bool)
    (done_counting: Signal dom Bool)
    (ack          : Signal dom Bool)
    : Signal dom (BitVec 1 × BitVec 1 × BitVec 1) :=
  -- Use Signal.loop to implement the FSM state register
  let state : Signal dom (BitVec 4) :=
    Signal.loop fun (state : Signal dom (BitVec 4)) =>
      -- State comparisons
      let isS    := state === (Signal.pure stS)
      let isS1   := state === (Signal.pure stS1)
      let isS11  := state === (Signal.pure stS11)
      let isS110 := state === (Signal.pure stS110)
      let isB0   := state === (Signal.pure stB0)
      let isB1   := state === (Signal.pure stB1)
      let isB2   := state === (Signal.pure stB2)
      let isB3   := state === (Signal.pure stB3)
      let isCount:= state === (Signal.pure stCount)
      let isWait := state === (Signal.pure stWait)
      -- Next state from S: data=1 → S1, data=0 → S
      let nextFromS    := Signal.mux data (Signal.pure stS1) (Signal.pure stS)
      -- Next state from S1: data=1 → S11, data=0 → S
      let nextFromS1   := Signal.mux data (Signal.pure stS11) (Signal.pure stS)
      -- Next state from S11: data=1 → S11, data=0 → S110
      let nextFromS11  := Signal.mux data (Signal.pure stS11) (Signal.pure stS110)
      -- Next state from S110: data=1 → B0, data=0 → S
      let nextFromS110 := Signal.mux data (Signal.pure stB0) (Signal.pure stS)
      -- B0 → B1 → B2 → B3 → Count (unconditional)
      let nextFromB0   := Signal.pure stB1
      let nextFromB1   := Signal.pure stB2
      let nextFromB2   := Signal.pure stB3
      let nextFromB3   := Signal.pure stCount
      -- Count: done_counting=1 → Wait, else → Count
      let nextFromCount := Signal.mux done_counting (Signal.pure stWait) (Signal.pure stCount)
      -- Wait: ack=1 → S, else → Wait
      let nextFromWait  := Signal.mux ack (Signal.pure stS) (Signal.pure stWait)
      -- Priority mux to select next state
      let nextState :=
        hw_cond (Signal.pure stS)
        | isS    => nextFromS
        | isS1   => nextFromS1
        | isS11  => nextFromS11
        | isS110 => nextFromS110
        | isB0   => nextFromB0
        | isB1   => nextFromB1
        | isB2   => nextFromB2
        | isB3   => nextFromB3
        | isCount => nextFromCount
        | isWait  => nextFromWait
      -- Apply synchronous reset
      let nextWithReset := Signal.mux reset (Signal.pure stS) nextState
      -- Register the state
      Signal.register stS nextWithReset
  -- Output logic (Moore outputs based on current state)
  -- shift_ena: high in B0, B1, B2, B3
  let inShift := (state === Signal.pure stB0) |||
                 (state === Signal.pure stB1) |||
                 (state === Signal.pure stB2) |||
                 (state === Signal.pure stB3)
  let shift_ena : Signal dom (BitVec 1) :=
    Signal.mux inShift (Signal.pure 1#1) (Signal.pure 0#1)
  -- counting: high in Count
  let inCount := state === Signal.pure stCount
  let counting : Signal dom (BitVec 1) :=
    Signal.mux inCount (Signal.pure 1#1) (Signal.pure 0#1)
  -- done: high in Wait
  let inWait := state === Signal.pure stWait
  let done : Signal dom (BitVec 1) :=
    Signal.mux inWait (Signal.pure 1#1) (Signal.pure 0#1)
  bundle2 shift_ena (bundle2 counting done)

#synthesizeVerilog prob151_review2015_fsm
