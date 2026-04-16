import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding for 3-state Mealy FSM
private abbrev stS   : BitVec 2 := 0#2  -- initial state
private abbrev stS1  : BitVec 2 := 1#2  -- seen "1"
private abbrev stS10 : BitVec 2 := 2#2  -- seen "10"

/-- Mealy FSM that detects overlapping sequence "101" on input x.
    Output z is asserted when "101" is completed (Mealy output).
    Active-low asynchronous reset (aresetn) modeled as synchronous mux.
    States: S=0 (init), S1=1 (saw 1), S10=2 (saw 10). -/
def prob129_ece241_2013_q8 {dom : DomainConfig}
    (aresetn : Signal dom Bool)
    (x : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  -- Use Signal.loop to maintain state feedback
  let state : Signal dom (BitVec 2) :=
    Signal.loop fun (state : Signal dom (BitVec 2)) =>
      let inS   := state === Signal.pure stS
      let inS1  := state === Signal.pure stS1
      let inS10 := state === Signal.pure stS10
      -- Next state from each state
      let nextFromS   := Signal.mux x (Signal.pure stS1) (Signal.pure stS)
      let nextFromS1  := Signal.mux x (Signal.pure stS1) (Signal.pure stS10)
      let nextFromS10 := Signal.mux x (Signal.pure stS1) (Signal.pure stS)
      -- Mux based on current state
      let nextState :=
        hw_cond (Signal.pure stS)
        | inS   => nextFromS
        | inS1  => nextFromS1
        | inS10 => nextFromS10
      -- Apply active-low async reset (modeled as sync mux): ~aresetn → S
      let nextWithReset := Signal.mux aresetn nextState (Signal.pure stS)
      Signal.register stS nextWithReset
  -- Mealy output: z = (state == S10) && x
  let inS10 := state === Signal.pure stS10
  Signal.mux (inS10 &&& x) (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob129_ece241_2013_q8
