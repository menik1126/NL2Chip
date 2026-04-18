import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding (6 states need 3 bits)
private abbrev stA : BitVec 3 := 0#3
private abbrev stB : BitVec 3 := 1#3
private abbrev stC : BitVec 3 := 2#3
private abbrev stD : BitVec 3 := 3#3
private abbrev stE : BitVec 3 := 4#3
private abbrev stF : BitVec 3 := 5#3

/-- FSM with 6 states implementing the 2012 Q2 state machine.
    Output z is high when in state E or F. -/
def prob138_2012_q2fsm {dom : DomainConfig}
    (reset : Signal dom Bool)
    (w : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  -- State register via Signal.loop
  let state : Signal dom (BitVec 3) :=
    Signal.loop fun (state : Signal dom (BitVec 3)) =>
      -- Next state logic based on current state and input w
      let isA := state === Signal.pure stA
      let isB := state === Signal.pure stB
      let isC := state === Signal.pure stC
      let isD := state === Signal.pure stD
      let isE := state === Signal.pure stE
      let isF := state === Signal.pure stF
      
      -- State transitions
      let nextFromA := Signal.mux w (Signal.pure stB) (Signal.pure stA)
      let nextFromB := Signal.mux w (Signal.pure stC) (Signal.pure stD)
      let nextFromC := Signal.mux w (Signal.pure stE) (Signal.pure stD)
      let nextFromD := Signal.mux w (Signal.pure stF) (Signal.pure stA)
      let nextFromE := Signal.mux w (Signal.pure stE) (Signal.pure stD)
      let nextFromF := Signal.mux w (Signal.pure stC) (Signal.pure stD)
      
      -- Mux chain to select next state
      let nextState := 
        Signal.mux isA nextFromA (
        Signal.mux isB nextFromB (
        Signal.mux isC nextFromC (
        Signal.mux isD nextFromD (
        Signal.mux isE nextFromE nextFromF))))
      
      -- Apply synchronous reset: reset → A
      let nextWithReset := Signal.mux reset (Signal.pure stA) nextState
      
      -- Register with initial value A
      Signal.register stA nextWithReset
  
  -- Output z = 1 when state is E or F
  let isE := state === Signal.pure stE
  let isF := state === Signal.pure stF
  let z_bool := isE ||| isF
  Signal.mux z_bool (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob138_2012_q2fsm
