import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding
private abbrev stA : BitVec 2 := 0#2
private abbrev stB : BitVec 2 := 1#2
private abbrev stC : BitVec 2 := 2#2
private abbrev stD : BitVec 2 := 3#2

/-- Moore FSM with 4 states (A, B, C, D). Synchronous reset to state A.
    Output is 1 only in state D. -/
def prob120_fsm3s {dom : DomainConfig}
    (reset : Signal dom Bool)
    (inp : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  -- State register via Signal.loop
  let state : Signal dom (BitVec 2) :=
    Signal.loop fun (state : Signal dom (BitVec 2)) =>
      -- Next state logic based on current state and input
      let isA := state === (Signal.pure stA)
      let isB := state === (Signal.pure stB)
      let isC := state === (Signal.pure stC)
      
      -- State A: in=0 → A, in=1 → B
      let nextFromA := Signal.mux inp (Signal.pure stB) (Signal.pure stA)
      -- State B: in=0 → C, in=1 → B
      let nextFromB := Signal.mux inp (Signal.pure stB) (Signal.pure stC)
      -- State C: in=0 → A, in=1 → D
      let nextFromC := Signal.mux inp (Signal.pure stD) (Signal.pure stA)
      -- State D: in=0 → C, in=1 → B
      let nextFromD := Signal.mux inp (Signal.pure stB) (Signal.pure stC)
      
      -- Select next state based on current state
      let nextState := 
        Signal.mux isA nextFromA
          (Signal.mux isB nextFromB
            (Signal.mux isC nextFromC nextFromD))
      
      -- Apply synchronous reset: reset → A
      let nextWithReset := Signal.mux reset (Signal.pure stA) nextState
      
      -- Register with initial value A
      Signal.register stA nextWithReset
  
  -- Output: 1 when state == D, 0 otherwise
  Signal.mux (state === (Signal.pure stD)) (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob120_fsm3s
