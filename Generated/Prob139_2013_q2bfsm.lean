import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding (4-bit to hold values 0-8)
private abbrev stA  : BitVec 4 := 0#4
private abbrev stB  : BitVec 4 := 1#4
private abbrev stS0 : BitVec 4 := 2#4
private abbrev stS1 : BitVec 4 := 3#4
private abbrev stS10 : BitVec 4 := 4#4
private abbrev stG1 : BitVec 4 := 5#4
private abbrev stG2 : BitVec 4 := 6#4
private abbrev stP0 : BitVec 4 := 7#4
private abbrev stP1 : BitVec 4 := 8#4

/-- Motor control FSM with synchronous active-low reset.
    States: A(0), B(1), S0(2), S1(3), S10(4), G1(5), G2(6), P0(7), P1(8).
    Output f=1 in state B; g=1 in states G1, G2, P1. -/
def prob139_2013_q2bfsm {dom : DomainConfig}
    (resetn : Signal dom Bool)
    (x : Signal dom Bool)
    (y : Signal dom Bool)
    : Signal dom (BitVec 1 × BitVec 1) :=
  let state : Signal dom (BitVec 4) :=
    Signal.loop fun (state : Signal dom (BitVec 4)) =>
      -- Decode current state
      let isA   := state === (Signal.pure stA)
      let isB   := state === (Signal.pure stB)
      let isS0  := state === (Signal.pure stS0)
      let isS1  := state === (Signal.pure stS1)
      let isS10 := state === (Signal.pure stS10)
      let isG1  := state === (Signal.pure stG1)
      let isG2  := state === (Signal.pure stG2)
      let isP0  := state === (Signal.pure stP0)
      -- Next state logic:
      --   A   → B
      --   B   → S0
      --   S0  → x ? S1 : S0
      --   S1  → x ? S1 : S10
      --   S10 → x ? G1 : S0
      --   G1  → y ? P1 : G2
      --   G2  → y ? P1 : P0
      --   P0  → P0
      --   P1  → P1
      let nextFromA   := Signal.pure stB
      let nextFromB   := Signal.pure stS0
      let nextFromS0  := Signal.mux x (Signal.pure stS1) (Signal.pure stS0)
      let nextFromS1  := Signal.mux x (Signal.pure stS1) (Signal.pure stS10)
      let nextFromS10 := Signal.mux x (Signal.pure stG1) (Signal.pure stS0)
      let nextFromG1  := Signal.mux y (Signal.pure stP1) (Signal.pure stG2)
      let nextFromG2  := Signal.mux y (Signal.pure stP1) (Signal.pure stP0)
      let nextFromP0  := Signal.pure stP0
      let nextFromP1  := Signal.pure stP1
      -- Mux over all states
      let nextState :=
        Signal.mux isA   nextFromA   (
        Signal.mux isB   nextFromB   (
        Signal.mux isS0  nextFromS0  (
        Signal.mux isS1  nextFromS1  (
        Signal.mux isS10 nextFromS10 (
        Signal.mux isG1  nextFromG1  (
        Signal.mux isG2  nextFromG2  (
        Signal.mux isP0  nextFromP0  nextFromP1)))))))
      -- Synchronous active-low reset: resetn=0 → go to A
      let nextWithReset := Signal.mux resetn nextState (Signal.pure stA)
      Signal.register stA nextWithReset
  -- Output logic
  -- f = (state == B)
  let isB_out := state === (Signal.pure stB)
  let f := Signal.mux isB_out (Signal.pure 1#1) (Signal.pure 0#1)
  -- g = (state == G1) || (state == G2) || (state == P1)
  let isG1_out := state === (Signal.pure stG1)
  let isG2_out := state === (Signal.pure stG2)
  let isP1_out := state === (Signal.pure stP1)
  let g_bool := isG1_out ||| isG2_out ||| isP1_out
  let g := Signal.mux g_bool (Signal.pure 1#1) (Signal.pure 0#1)
  bundle2 f g

#synthesizeVerilog prob139_2013_q2bfsm
