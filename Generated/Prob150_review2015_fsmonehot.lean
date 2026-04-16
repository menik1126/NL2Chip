import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- One-hot FSM next-state and output logic for a Moore machine with states
    S, S1, S11, S110, B0, B1, B2, B3, Count, Wait.
    Inputs: d, done_counting, ack, state (10-bit one-hot).
    Outputs: B3_next, S_next, S1_next, Count_next, Wait_next, done, counting, shift_ena -/
def prob150_review2015_fsmonehot {dom : DomainConfig}
    (d : Signal dom Bool)
    (done_counting : Signal dom Bool)
    (ack : Signal dom Bool)
    (state : Signal dom (BitVec 10))
    : Signal dom (BitVec 1 × BitVec 1 × BitVec 1 × BitVec 1 × BitVec 1 × BitVec 1 × BitVec 1 × BitVec 1) :=
  -- One-hot encoding: S=0, S1=1, S11=2, S110=3, B0=4, B1=5, B2=6, B3=7, Count=8, Wait=9
  -- Extract individual state bits as BitVec 1 signals using extractLsb'
  let sS    : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 0 1) state  -- S
  let sS1   : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 1 1) state  -- S1
  let sS110 : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 3 1) state  -- S110
  let sB0   : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 4 1) state  -- B0
  let sB1   : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 5 1) state  -- B1
  let sB2   : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 6 1) state  -- B2
  let sB3   : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 7 1) state  -- B3
  let sCount: Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 8 1) state  -- Count
  let sWait : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 9 1) state  -- Wait

  -- Convert d, done_counting, ack to BitVec 1
  let dBv        : Signal dom (BitVec 1) := Signal.mux d (Signal.pure 1#1) (Signal.pure 0#1)
  let dcBv       : Signal dom (BitVec 1) := Signal.mux done_counting (Signal.pure 1#1) (Signal.pure 0#1)
  let ackBv      : Signal dom (BitVec 1) := Signal.mux ack (Signal.pure 1#1) (Signal.pure 0#1)
  let not_dBv    : Signal dom (BitVec 1) := ~~~dBv
  let not_dcBv   : Signal dom (BitVec 1) := ~~~dcBv
  let not_ackBv  : Signal dom (BitVec 1) := ~~~ackBv

  -- B3_next = state[B2]
  let b3_next : Signal dom (BitVec 1) := sB2

  -- S_next = state[S]&~d | state[S1]&~d | state[S110]&~d | state[Wait]&ack
  let s_next : Signal dom (BitVec 1) :=
    (sS &&& not_dBv) ||| (sS1 &&& not_dBv) ||| (sS110 &&& not_dBv) ||| (sWait &&& ackBv)

  -- S1_next = state[S]&d
  let s1_next : Signal dom (BitVec 1) := sS &&& dBv

  -- Count_next = state[B3] | state[Count]&~done_counting
  let count_next : Signal dom (BitVec 1) := sB3 ||| (sCount &&& not_dcBv)

  -- Wait_next = state[Count]&done_counting | state[Wait]&~ack
  let wait_next : Signal dom (BitVec 1) := (sCount &&& dcBv) ||| (sWait &&& not_ackBv)

  -- done = state[Wait]
  let done_out : Signal dom (BitVec 1) := sWait

  -- counting = state[Count]
  let counting_out : Signal dom (BitVec 1) := sCount

  -- shift_ena = |state[B3:B0] = state[4]|state[5]|state[6]|state[7]
  let shift_ena_out : Signal dom (BitVec 1) := sB0 ||| sB1 ||| sB2 ||| sB3

  -- Bundle all 8 outputs
  bundle2 b3_next (bundle2 s_next (bundle2 s1_next (bundle2 count_next (bundle2 wait_next (bundle2 done_out (bundle2 counting_out shift_ena_out))))))

#synthesizeVerilog prob150_review2015_fsmonehot
