import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding
private abbrev stS   : BitVec 2 := 0#2
private abbrev stS1  : BitVec 2 := 1#2
private abbrev stS10 : BitVec 2 := 2#2

/-- Mealy FSM: detects "101" sequence with 3 states.
    Output z is asserted when "101" is detected (Mealy: depends on state and input).
    Async reset (active low) to state S. -/
def prob129_ece241_2013_q8 {dom : DomainConfig}
    (aresetn : Signal dom Bool)
    (x : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  let state : Signal dom (BitVec 2) :=
    Signal.loop fun (state : Signal dom (BitVec 2)) =>
      -- Next state logic
      let isS   := state === (Signal.pure stS)
      let isS1  := state === (Signal.pure stS1)
      let isS10 := state === (Signal.pure stS10)
      
      -- State transitions:
      -- S:   x=1 → S1,  x=0 → S
      -- S1:  x=1 → S1,  x=0 → S10
      -- S10: x=1 → S1,  x=0 → S
      let nextFromS   := Signal.mux x (Signal.pure stS1) (Signal.pure stS)
      let nextFromS1  := Signal.mux x (Signal.pure stS1) (Signal.pure stS10)
      let nextFromS10 := Signal.mux x (Signal.pure stS1) (Signal.pure stS)
      
      let nextState := 
        hw_cond (Signal.pure stS)
        | isS   => nextFromS
        | isS1  => nextFromS1
        | isS10 => nextFromS10
      
      -- Apply async reset (active low): !aresetn → S
      let nextWithReset := Signal.mux aresetn nextState (Signal.pure stS)
      
      -- Register with initial value S
      Signal.register stS nextWithReset
  
  -- Output logic (Mealy: depends on state and input)
  -- S:   z = 0
  -- S1:  z = 0
  -- S10: z = x (output 1 when x=1, completing "101")
  let isS10 := state === (Signal.pure stS10)
  let z_bool := isS10 &&& x
  
  -- Convert Bool to BitVec 1
  Signal.mux z_bool (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob129_ece241_2013_q8
