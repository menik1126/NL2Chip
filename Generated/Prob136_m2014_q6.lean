import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding (3 bits for 6 states)
private abbrev stA : BitVec 3 := 0#3
private abbrev stB : BitVec 3 := 1#3
private abbrev stC : BitVec 3 := 2#3
private abbrev stD : BitVec 3 := 3#3
private abbrev stE : BitVec 3 := 4#3
private abbrev stF : BitVec 3 := 5#3

/-- FSM with 6 states implementing the state machine from m2014_q6.
    Output z is 1 when in state E or F. -/
def prob136_m2014_q6 {dom : DomainConfig}
    (reset : Signal dom Bool)
    (w : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  -- State register via Signal.loop
  let state : Signal dom (BitVec 3) :=
    Signal.loop fun (state : Signal dom (BitVec 3)) =>
      -- Next state logic based on current state and input w
      -- A: w=1 → A, w=0 → B
      -- B: w=1 → D, w=0 → C
      -- C: w=1 → D, w=0 → E
      -- D: w=1 → A, w=0 → F
      -- E: w=1 → D, w=0 → E
      -- F: w=1 → D, w=0 → C
      
      let isA := state === Signal.pure stA
      let isB := state === Signal.pure stB
      let isC := state === Signal.pure stC
      let isD := state === Signal.pure stD
      let isE := state === Signal.pure stE
      let isF := state === Signal.pure stF
      
      -- Next state for each current state
      let nextFromA := Signal.mux w (Signal.pure stA) (Signal.pure stB)
      let nextFromB := Signal.mux w (Signal.pure stD) (Signal.pure stC)
      let nextFromC := Signal.mux w (Signal.pure stD) (Signal.pure stE)
      let nextFromD := Signal.mux w (Signal.pure stA) (Signal.pure stF)
      let nextFromE := Signal.mux w (Signal.pure stD) (Signal.pure stE)
      let nextFromF := Signal.mux w (Signal.pure stD) (Signal.pure stC)
      
      -- Mux tree to select next state based on current state
      let nextState := 
        Signal.mux isA nextFromA (
          Signal.mux isB nextFromB (
            Signal.mux isC nextFromC (
              Signal.mux isD nextFromD (
                Signal.mux isE nextFromE nextFromF
              )
            )
          )
        )
      
      -- Apply reset: reset → A
      let nextWithReset := Signal.mux reset (Signal.pure stA) nextState
      
      -- Register with initial value A
      Signal.register stA nextWithReset
  
  -- Output z = 1 when state is E or F
  let isE := state === Signal.pure stE
  let isF := state === Signal.pure stF
  let z_bool := isE ||| isF
  Signal.mux z_bool (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob136_m2014_q6
