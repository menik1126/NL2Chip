import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- One-hot FSM combinational logic for review2015 problem -/
def prob150_review2015_fsmonehot {dom : DomainConfig}
    (d done_counting ack : Signal dom Bool)
    (state : Signal dom (BitVec 10))
    : Signal dom (((BitVec 1 × BitVec 1) × (BitVec 1 × BitVec 1)) × ((BitVec 1 × BitVec 1) × (BitVec 1 × BitVec 1))) :=
  -- Extract state bits (one-hot encoding) as BitVec 1
  let s_S     := Signal.map (fun x => BitVec.extractLsb 0 0 x) state
  let s_S1    := Signal.map (fun x => BitVec.extractLsb 1 1 x) state
  let s_S110  := Signal.map (fun x => BitVec.extractLsb 3 3 x) state
  let s_B0    := Signal.map (fun x => BitVec.extractLsb 4 4 x) state
  let s_B1    := Signal.map (fun x => BitVec.extractLsb 5 5 x) state
  let s_B2    := Signal.map (fun x => BitVec.extractLsb 6 6 x) state
  let s_B3    := Signal.map (fun x => BitVec.extractLsb 7 7 x) state
  let s_Count := Signal.map (fun x => BitVec.extractLsb 8 8 x) state
  let s_Wait  := Signal.map (fun x => BitVec.extractLsb 9 9 x) state
  
  -- Convert Bool inputs to BitVec 1 for operations
  let d_bv := Signal.mux d (Signal.pure 1#1) (Signal.pure 0#1)
  let done_counting_bv := Signal.mux done_counting (Signal.pure 1#1) (Signal.pure 0#1)
  let ack_bv := Signal.mux ack (Signal.pure 1#1) (Signal.pure 0#1)
  
  -- B3_next = state[B2]
  let B3_next := s_B2
  
  -- S_next = state[S]&~d | state[S1]&~d | state[S110]&~d | state[Wait]&ack
  let not_d := ~~~d_bv
  let S_next := (s_S &&& not_d) ||| (s_S1 &&& not_d) ||| (s_S110 &&& not_d) ||| (s_Wait &&& ack_bv)
  
  -- S1_next = state[S]&d
  let S1_next := s_S &&& d_bv
  
  -- Count_next = state[B3] | state[Count]&~done_counting
  let not_done_counting := ~~~done_counting_bv
  let Count_next := s_B3 ||| (s_Count &&& not_done_counting)
  
  -- Wait_next = state[Count]&done_counting | state[Wait]&~ack
  let not_ack := ~~~ack_bv
  let Wait_next := (s_Count &&& done_counting_bv) ||| (s_Wait &&& not_ack)
  
  -- done = state[Wait]
  let done := s_Wait
  
  -- counting = state[Count]
  let counting := s_Count
  
  -- shift_ena = |state[B3:B0]
  let shift_ena := s_B0 ||| s_B1 ||| s_B2 ||| s_B3
  
  -- Bundle: B3_next, S_next, S1_next, Count_next, Wait_next, done, counting, shift_ena
  bundle2 (bundle2 (bundle2 B3_next S_next) (bundle2 S1_next Count_next))
          (bundle2 (bundle2 Wait_next done) (bundle2 counting shift_ena))

#synthesizeVerilog prob150_review2015_fsmonehot
