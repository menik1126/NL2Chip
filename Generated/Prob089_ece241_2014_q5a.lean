import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding: A=0, B=1, C=2
private abbrev stA : BitVec 2 := 0#2
private abbrev stB : BitVec 2 := 1#2
private abbrev stC : BitVec 2 := 2#2

/-- 2's complementer Moore state machine with 3 states -/
def prob089_ece241_2014_q5a {dom : DomainConfig}
    (areset : Signal dom Bool)
    (x : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  let state : Signal dom (BitVec 2) :=
    Signal.loop fun (state : Signal dom (BitVec 2)) =>
      -- Next state logic based on current state and input x
      let isA := state === (Signal.pure stA)
      let isB := state === (Signal.pure stB)
      
      -- State A: x=0 → A, x=1 → C
      let nextFromA := Signal.mux x (Signal.pure stC) (Signal.pure stA)
      
      -- State B: x=0 → C, x=1 → B
      let nextFromB := Signal.mux x (Signal.pure stB) (Signal.pure stC)
      
      -- State C: x=0 → C, x=1 → B
      let nextFromC := Signal.mux x (Signal.pure stB) (Signal.pure stC)
      
      -- Mux between states
      let nextState := Signal.mux isA nextFromA
                        (Signal.mux isB nextFromB nextFromC)
      
      -- Apply async reset to state A
      let nextWithReset := Signal.mux areset (Signal.pure stA) nextState
      
      -- Register with initial value A
      Signal.register stA nextWithReset
  
  -- Output z = 1 when state == C
  let isStateC := state === (Signal.pure stC)
  Signal.mux isStateC (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob089_ece241_2014_q5a
