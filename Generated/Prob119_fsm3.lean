import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding: A=0, B=1, C=2, D=3
private abbrev stA : BitVec 2 := 0#2
private abbrev stB : BitVec 2 := 1#2
private abbrev stC : BitVec 2 := 2#2
private abbrev stD : BitVec 2 := 3#2

/-- Moore FSM with 4 states (A=0, B=1, C=2, D=3).
    State transitions:
      A + in=0 → A,  A + in=1 → B  (output 0)
      B + in=0 → C,  B + in=1 → B  (output 0)
      C + in=0 → A,  C + in=1 → D  (output 0)
      D + in=0 → C,  D + in=1 → B  (output 1)
    Async reset to state A (modeled as synchronous mux). -/
def prob119_fsm3 {dom : DomainConfig}
    (areset : Signal dom Bool)
    (inp : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  -- State register via Signal.loop
  let state : Signal dom (BitVec 2) :=
    Signal.loop fun (state : Signal dom (BitVec 2)) =>
      let isA := state === (Signal.pure stA)
      let isB := state === (Signal.pure stB)
      let isC := state === (Signal.pure stC)
      -- Next state logic per state
      let nextFromA := Signal.mux inp (Signal.pure stB) (Signal.pure stA)
      let nextFromB := Signal.mux inp (Signal.pure stB) (Signal.pure stC)
      let nextFromC := Signal.mux inp (Signal.pure stD) (Signal.pure stA)
      let nextFromD := Signal.mux inp (Signal.pure stB) (Signal.pure stC)
      -- Select next state based on current state
      let nextState :=
        Signal.mux isA nextFromA
          (Signal.mux isB nextFromB
            (Signal.mux isC nextFromC
              nextFromD))
      -- Apply async reset (modeled as sync mux): areset → A
      let nextWithReset := Signal.mux areset (Signal.pure stA) nextState
      -- State register initialized to A
      Signal.register stA nextWithReset
  -- Output: 1 when in state D
  Signal.mux (state === Signal.pure stD) (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob119_fsm3
