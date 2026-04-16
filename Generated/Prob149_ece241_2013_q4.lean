import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

private abbrev stA2 : BitVec 3 := 0#3
private abbrev stB1 : BitVec 3 := 1#3
private abbrev stB2 : BitVec 3 := 2#3
private abbrev stC1 : BitVec 3 := 3#3
private abbrev stC2 : BitVec 3 := 4#3
private abbrev stD1 : BitVec 3 := 5#3

/-- Water reservoir flow controller FSM.
    States encode water level and direction of change.
    Outputs {fr2, fr1, fr0, dfr} packed as BitVec 4. -/
def prob149_ece241_2013_q4 {dom : DomainConfig}
    (reset : Signal dom Bool)
    (s : Signal dom (BitVec 3))
    : Signal dom (BitVec 4) :=
  -- Extract sensor bits using extractLsb' (which is compiler-supported)
  let s0_bv : Signal dom (BitVec 1) := Signal.map (fun x => BitVec.extractLsb' 0 1 x) s
  let s1_bv : Signal dom (BitVec 1) := Signal.map (fun x => BitVec.extractLsb' 1 1 x) s
  let s2_bv : Signal dom (BitVec 1) := Signal.map (fun x => BitVec.extractLsb' 2 1 x) s
  -- Convert to Bool by comparing with 1
  let s0 : Signal dom Bool := s0_bv === Signal.pure 1#1
  let s1 : Signal dom Bool := s1_bv === Signal.pure 1#1
  let s2 : Signal dom Bool := s2_bv === Signal.pure 1#1
  -- Compute the state register using Signal.loop
  let state : Signal dom (BitVec 3) :=
    Signal.loop fun (state : Signal dom (BitVec 3)) =>
      -- Next state logic
      let isA2 := state === Signal.pure stA2
      let isB1 := state === Signal.pure stB1
      let isB2 := state === Signal.pure stB2
      let isC1 := state === Signal.pure stC1
      let isC2 := state === Signal.pure stC2
      let isD1 := state === Signal.pure stD1
      -- A2: next = s[0] ? B1 : A2
      let nextA2 := Signal.mux s0 (Signal.pure stB1) (Signal.pure stA2)
      -- B1: next = s[1] ? C1 : (s[0] ? B1 : A2)
      let nextB1 := Signal.mux s1 (Signal.pure stC1) (Signal.mux s0 (Signal.pure stB1) (Signal.pure stA2))
      -- B2: next = s[1] ? C1 : (s[0] ? B2 : A2)
      let nextB2 := Signal.mux s1 (Signal.pure stC1) (Signal.mux s0 (Signal.pure stB2) (Signal.pure stA2))
      -- C1: next = s[2] ? D1 : (s[1] ? C1 : B2)
      let nextC1 := Signal.mux s2 (Signal.pure stD1) (Signal.mux s1 (Signal.pure stC1) (Signal.pure stB2))
      -- C2: next = s[2] ? D1 : (s[1] ? C2 : B2)
      let nextC2 := Signal.mux s2 (Signal.pure stD1) (Signal.mux s1 (Signal.pure stC2) (Signal.pure stB2))
      -- D1: next = s[2] ? D1 : C2
      let nextD1 := Signal.mux s2 (Signal.pure stD1) (Signal.pure stC2)
      -- Combine next state: priority mux through all states
      let nextState :=
        Signal.mux isA2 nextA2 (
        Signal.mux isB1 nextB1 (
        Signal.mux isB2 nextB2 (
        Signal.mux isC1 nextC1 (
        Signal.mux isC2 nextC2 (
        Signal.mux isD1 nextD1 (Signal.pure stA2))))))
      -- Apply synchronous reset: reset → A2
      let nextWithReset := Signal.mux reset (Signal.pure stA2) nextState
      -- Register state with initial value A2
      Signal.register stA2 nextWithReset
  -- Output logic based on current state
  -- A2: 4'b1111, B1: 4'b0110, B2: 4'b0111
  -- C1: 4'b0010, C2: 4'b0011, D1: 4'b0000
  let outIsA2 := state === Signal.pure stA2
  let outIsB1 := state === Signal.pure stB1
  let outIsB2 := state === Signal.pure stB2
  let outIsC1 := state === Signal.pure stC1
  let outIsC2 := state === Signal.pure stC2
  Signal.mux outIsA2 (Signal.pure 15#4) (
  Signal.mux outIsB1 (Signal.pure 6#4) (
  Signal.mux outIsB2 (Signal.pure 7#4) (
  Signal.mux outIsC1 (Signal.pure 2#4) (
  Signal.mux outIsC2 (Signal.pure 3#4) (
  Signal.pure 0#4)))))

#synthesizeVerilog prob149_ece241_2013_q4
