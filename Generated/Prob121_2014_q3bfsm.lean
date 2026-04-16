import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding (3-bit)
-- A = 000, B = 001, C = 010, D = 011, E = 100
private abbrev stA : BitVec 3 := 0#3
private abbrev stB : BitVec 3 := 1#3
private abbrev stC : BitVec 3 := 2#3
private abbrev stD : BitVec 3 := 3#3
private abbrev stE : BitVec 3 := 4#3

/-- FSM with 5 states (A-E), synchronous reset to state A (000).
    Transition table:
      A + x=0 → A,  A + x=1 → B,  z=0
      B + x=0 → B,  B + x=1 → E,  z=0
      C + x=0 → C,  C + x=1 → B,  z=0
      D + x=0 → B,  D + x=1 → C,  z=1
      E + x=0 → D,  E + x=1 → E,  z=1 -/
def prob121_2014_q3bfsm {dom : DomainConfig}
    (reset : Signal dom Bool)
    (x : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  -- State register via Signal.loop
  let state : Signal dom (BitVec 3) :=
    Signal.loop fun (state : Signal dom (BitVec 3)) =>
      let isA := state === Signal.pure stA
      let isB := state === Signal.pure stB
      let isC := state === Signal.pure stC
      let isD := state === Signal.pure stD
      -- Next state when x=0: A→A, B→B, C→C, D→B, E→D
      let nextFromA_x0 := Signal.pure stA
      let nextFromB_x0 := Signal.pure stB
      let nextFromC_x0 := Signal.pure stC
      let nextFromD_x0 := Signal.pure stB
      let nextFromE_x0 := Signal.pure stD
      -- Next state when x=1: A→B, B→E, C→B, D→C, E→E
      let nextFromA_x1 := Signal.pure stB
      let nextFromB_x1 := Signal.pure stE
      let nextFromC_x1 := Signal.pure stB
      let nextFromD_x1 := Signal.pure stC
      let nextFromE_x1 := Signal.pure stE
      -- Select next state for x=0
      let nextX0 :=
        Signal.mux isA nextFromA_x0
          (Signal.mux isB nextFromB_x0
            (Signal.mux isC nextFromC_x0
              (Signal.mux isD nextFromD_x0
                nextFromE_x0)))
      -- Select next state for x=1
      let nextX1 :=
        Signal.mux isA nextFromA_x1
          (Signal.mux isB nextFromB_x1
            (Signal.mux isC nextFromC_x1
              (Signal.mux isD nextFromD_x1
                nextFromE_x1)))
      -- Select based on x
      let nextState := Signal.mux x nextX1 nextX0
      -- Apply synchronous reset: if reset, go to A
      let nextWithReset := Signal.mux reset (Signal.pure stA) nextState
      -- State register initialized to A
      Signal.register stA nextWithReset
  -- Output z: 1 when in state D or E
  let isD := state === Signal.pure stD
  let isE := state === Signal.pure stE
  let inDE := isD ||| isE
  Signal.mux inDE (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob121_2014_q3bfsm
