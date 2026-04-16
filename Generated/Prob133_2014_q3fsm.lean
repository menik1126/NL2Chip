import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding (3 bits)
-- A=0, B=1, C=2, S10=3, S11=4, S20=5, S21=6, S22=7
private abbrev stA   : BitVec 3 := 0#3
private abbrev stB   : BitVec 3 := 1#3
private abbrev stC   : BitVec 3 := 2#3
private abbrev stS10 : BitVec 3 := 3#3
private abbrev stS11 : BitVec 3 := 4#3
private abbrev stS20 : BitVec 3 := 5#3
private abbrev stS21 : BitVec 3 := 6#3
private abbrev stS22 : BitVec 3 := 7#3

/-- FSM for 2014_q3: counts w=1 in 3-cycle windows; z=1 iff exactly 2 ones in prior window.
    States: A=0 (reset), B=1 (start), C=2 (z=1 output), S10=3, S11=4, S20=5, S21=6, S22=7.
    Synchronous active-high reset. -/
def prob133_2014_q3fsm {dom : DomainConfig}
    (reset : Signal dom Bool)
    (s : Signal dom Bool)
    (w : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  -- Use Signal.loop to maintain the state register with feedback
  let state : Signal dom (BitVec 3) :=
    Signal.loop fun (state : Signal dom (BitVec 3)) =>
      -- Decode current state
      let isA   := state === Signal.pure stA
      let isB   := state === Signal.pure stB
      let isC   := state === Signal.pure stC
      let isS10 := state === Signal.pure stS10
      let isS11 := state === Signal.pure stS11
      let isS20 := state === Signal.pure stS20
      let isS21 := state === Signal.pure stS21
      let isS22 := state === Signal.pure stS22
      -- Next state logic per state
      -- A: next = s ? B : A
      let nextA   := Signal.mux s (Signal.pure stB) (Signal.pure stA)
      -- B: next = w ? S11 : S10
      let nextB   := Signal.mux w (Signal.pure stS11) (Signal.pure stS10)
      -- C: next = w ? S11 : S10  (same as B)
      let nextC   := Signal.mux w (Signal.pure stS11) (Signal.pure stS10)
      -- S10: next = w ? S21 : S20
      let nextS10 := Signal.mux w (Signal.pure stS21) (Signal.pure stS20)
      -- S11: next = w ? S22 : S21
      let nextS11 := Signal.mux w (Signal.pure stS22) (Signal.pure stS21)
      -- S20: next = B
      let nextS20 := Signal.pure stB
      -- S21: next = w ? C : B
      let nextS21 := Signal.mux w (Signal.pure stC) (Signal.pure stB)
      -- S22: next = w ? B : C
      let nextS22 := Signal.mux w (Signal.pure stB) (Signal.pure stC)
      -- Select next state using priority mux chain
      let nextState :=
        hw_cond (Signal.pure stA)
        | isA   => nextA
        | isB   => nextB
        | isC   => nextC
        | isS10 => nextS10
        | isS11 => nextS11
        | isS20 => nextS20
        | isS21 => nextS21
        | isS22 => nextS22
      -- Apply synchronous reset
      let nextWithReset := Signal.mux reset (Signal.pure stA) nextState
      -- Register the state
      Signal.register stA nextWithReset
  -- Output z = (state == C)
  Signal.mux (state === Signal.pure stC) (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob133_2014_q3fsm
