import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- One-hot FSM combinational logic: computes next_state (4 bits) and out (1 bit)
    for a Moore FSM with one-hot states A=0001, B=0010, C=0100, D=1000.
    State transitions:
      A: in=0 → A, in=1 → B, out=0
      B: in=0 → C, in=1 → B, out=0
      C: in=0 → A, in=1 → D, out=0
      D: in=0 → C, in=1 → B, out=1
    Derived one-hot equations:
      next_state[0] (A) = (state[A] | state[C]) & ~in
      next_state[1] (B) = (state[A] | state[B] | state[D]) & in
      next_state[2] (C) = (state[B] | state[D]) & ~in
      next_state[3] (D) = state[C] & in
      out = state[D] = state[3]
-/
def prob079_fsm3onehot {dom : DomainConfig}
    (inp : Signal dom Bool)
    (state : Signal dom (BitVec 4))
    : Signal dom (BitVec 4 × BitVec 1) :=
  -- Extract individual bits as BitVec 1 (one-hot: A=bit0, B=bit1, C=bit2, D=bit3)
  let stA : Signal dom (BitVec 1) := Signal.map (fun s => s.extractLsb' 0 1) state
  let stB : Signal dom (BitVec 1) := Signal.map (fun s => s.extractLsb' 1 1) state
  let stC : Signal dom (BitVec 1) := Signal.map (fun s => s.extractLsb' 2 1) state
  let stD : Signal dom (BitVec 1) := Signal.map (fun s => s.extractLsb' 3 1) state
  -- Convert inp (Bool) to BitVec 1 for bitwise ops
  let i    : Signal dom (BitVec 1) := Signal.mux inp (Signal.pure 1#1) (Signal.pure 0#1)
  let noti : Signal dom (BitVec 1) := ~~~i
  -- Next state bit logic (all BitVec 1):
  -- nsA = (stA | stC) & ~in
  let nsA : Signal dom (BitVec 1) := (stA ||| stC) &&& noti
  -- nsB = (stA | stB | stD) & in
  let nsB : Signal dom (BitVec 1) := (stA ||| stB ||| stD) &&& i
  -- nsC = (stB | stD) & ~in
  let nsC : Signal dom (BitVec 1) := (stB ||| stD) &&& noti
  -- nsD = stC & in
  let nsD : Signal dom (BitVec 1) := stC &&& i
  -- out = state[D]
  let out  : Signal dom (BitVec 1) := stD
  -- Build 4-bit next_state: {nsD, nsC, nsB, nsA} (bit3=nsD, bit0=nsA)
  let next_state : Signal dom (BitVec 4) := nsD ++ nsC ++ nsB ++ nsA
  bundle2 next_state out

#synthesizeVerilog prob079_fsm3onehot
