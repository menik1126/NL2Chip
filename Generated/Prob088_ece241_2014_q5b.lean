import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding: A = 0, B = 1
private abbrev stA : BitVec 1 := 0#1
private abbrev stB : BitVec 1 := 1#1

/-- Mealy FSM: 2's complementer.
    State A (reset): x=0 → A, z=0; x=1 → B, z=1
    State B: x=0 → B, z=1; x=1 → B, z=0
    Async active-high reset to state A. -/
def prob088_ece241_2014_q5b {dom : DomainConfig}
    (areset : Signal dom Bool)
    (x : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  -- Register the state with feedback
  let state := Signal.loop fun (state : Signal dom (BitVec 1)) =>
    let inStateA := state === (Signal.pure stA)
    -- Next state: A+x=0→A, A+x=1→B, B+*→B
    let nextFromA := Signal.mux x (Signal.pure stB) (Signal.pure stA)
    let nextState := Signal.mux inStateA nextFromA (Signal.pure stB)
    -- Async reset: areset → A
    let nextWithReset := Signal.mux areset (Signal.pure stA) nextState
    Signal.register stA nextWithReset
  -- Mealy output z:
  --   state=A, x=1 → z=1
  --   state=B, x=0 → z=1
  --   otherwise z=0
  let inStateA := state === (Signal.pure stA)
  let notX := ~~~x
  let zOut_bool := (inStateA &&& x) ||| (~~~inStateA &&& notX)
  Signal.mux zOut_bool (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob088_ece241_2014_q5b
