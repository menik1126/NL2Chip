import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding: A=0, B=1, C=2 (2-bit)
private abbrev stA : BitVec 2 := 0#2
private abbrev stB : BitVec 2 := 1#2
private abbrev stC : BitVec 2 := 2#2

/-- Serial 2's complementer Moore FSM.
    States: A=0 (output=0, initial), B=1 (output=0), C=2 (output=1).
    Transitions:
      A + x=0 → A,  A + x=1 → C
      B + x=0 → C,  B + x=1 → B
      C + x=0 → C,  C + x=1 → B
    Output z = 1 when in state C.
    Async reset (modeled as sync mux) to state A. -/
def prob089_ece241_2014_q5a {dom : DomainConfig}
    (areset : Signal dom Bool)
    (x : Signal dom Bool)
    : Signal dom Bool :=
  -- State register via Signal.loop
  let state : Signal dom (BitVec 2) :=
    Signal.loop fun (state : Signal dom (BitVec 2)) =>
      let isA := state === (Signal.pure stA)
      let isB := state === (Signal.pure stB)
      -- Next state logic:
      --   A + x=0 → A,  A + x=1 → C
      --   B + x=0 → C,  B + x=1 → B
      --   C + x=0 → C,  C + x=1 → B
      let nextFromA := Signal.mux x (Signal.pure stC) (Signal.pure stA)
      let nextFromB := Signal.mux x (Signal.pure stB) (Signal.pure stC)
      let nextFromC := Signal.mux x (Signal.pure stB) (Signal.pure stC)
      -- Select next state based on current state
      let nextState := Signal.mux isA nextFromA
                        (Signal.mux isB nextFromB nextFromC)
      -- Apply async reset (modeled as sync): areset → A
      let nextWithReset := Signal.mux areset (Signal.pure stA) nextState
      -- Register with initial value A
      Signal.register stA nextWithReset
  -- Output: z = (state == C)
  state === (Signal.pure stC)

#synthesizeVerilog prob089_ece241_2014_q5a
