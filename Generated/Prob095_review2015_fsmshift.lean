import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding: need 5 states (B0, B1, B2, B3, Done)
-- Use 3 bits to encode 5 states
private abbrev stB0   : BitVec 3 := 0#3
private abbrev stB1   : BitVec 3 := 1#3
private abbrev stB2   : BitVec 3 := 2#3
private abbrev stB3   : BitVec 3 := 3#3
private abbrev stDone : BitVec 3 := 4#3

/-- FSM for shift register control: asserts shift_ena for 4 cycles on reset, then 0 forever -/
def prob095_review2015_fsmshift {dom : DomainConfig}
    (reset : Signal dom Bool) : Signal dom (BitVec 1) :=
  let state : Signal dom (BitVec 3) :=
    Signal.loop fun (state : Signal dom (BitVec 3)) =>
      -- Next state logic
      let isB0   := state === Signal.pure stB0
      let isB1   := state === Signal.pure stB1
      let isB2   := state === Signal.pure stB2
      let isB3   := state === Signal.pure stB3
      
      -- State transitions: B0 -> B1 -> B2 -> B3 -> Done -> Done
      let nextState := 
        Signal.mux isB0 (Signal.pure stB1)
          (Signal.mux isB1 (Signal.pure stB2)
            (Signal.mux isB2 (Signal.pure stB3)
              (Signal.mux isB3 (Signal.pure stDone)
                (Signal.pure stDone))))  -- Done stays in Done
      
      -- Apply reset: reset -> B0
      let nextWithReset := Signal.mux reset (Signal.pure stB0) nextState
      
      -- Register with initial value B0
      Signal.register stB0 nextWithReset
  
  -- Output: shift_ena = (state == B0 || state == B1 || state == B2 || state == B3)
  -- i.e., shift_ena is low only when state == Done (4)
  let isDone := state === Signal.pure stDone
  let shift_ena_bool := ~~~isDone
  
  -- Convert Bool to BitVec 1 using Signal.mux
  Signal.mux shift_ena_bool (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob095_review2015_fsmshift
