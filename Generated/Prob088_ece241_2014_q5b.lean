import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding
private abbrev stA : BitVec 1 := 0#1
private abbrev stB : BitVec 1 := 1#1

/-- Mealy FSM implementing 2's complementer.
    State A: pass through bits until first 1
    State B: invert all subsequent bits
    Async reset to state A. -/
def prob088_ece241_2014_q5b {dom : DomainConfig}
    (areset : Signal dom Bool)
    (x : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  -- State register via Signal.loop
  let state : Signal dom (BitVec 1) :=
    Signal.loop fun (state : Signal dom (BitVec 1)) =>
      let isA := state === (Signal.pure stA)
      
      -- Next state logic:
      --   A + x=0 → A
      --   A + x=1 → B
      --   B + any → B
      let nextFromA := Signal.mux x (Signal.pure stB) (Signal.pure stA)
      let nextFromB := Signal.pure stB
      let nextState := Signal.mux isA nextFromA nextFromB
      
      -- Apply async reset (modeled as sync): areset → A
      let nextWithReset := Signal.mux areset (Signal.pure stA) nextState
      
      -- Register with initial value A
      Signal.register stA nextWithReset
  
  -- Mealy output logic (depends on current state and input):
  --   A + x=0 → z=0
  --   A + x=1 → z=1
  --   B + x=0 → z=1
  --   B + x=1 → z=0
  -- This is: z = (isA && x) || (isB && !x)
  -- Or: z = isA ? x : !x
  let isA := state === (Signal.pure stA)
  let xAsBit := Signal.mux x (Signal.pure 1#1) (Signal.pure 0#1)
  let notX := ~~~xAsBit
  Signal.mux isA xAsBit notX

#synthesizeVerilog prob088_ece241_2014_q5b
