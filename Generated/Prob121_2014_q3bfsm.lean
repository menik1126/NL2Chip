import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding
private abbrev stA : BitVec 3 := 0#3  -- 000
private abbrev stB : BitVec 3 := 1#3  -- 001
private abbrev stC : BitVec 3 := 2#3  -- 010
private abbrev stD : BitVec 3 := 3#3  -- 011
private abbrev stE : BitVec 3 := 4#3  -- 100

/-- FSM with 5 states implementing the given state transition table.
    Synchronous active-high reset to state 000.
    Output z is 1 for states 011 and 100. -/
def prob121_2014_q3bfsm {dom : DomainConfig}
    (reset : Signal dom Bool)
    (x : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  let state : Signal dom (BitVec 3) :=
    Signal.loop fun (state : Signal dom (BitVec 3)) =>
      -- Next state logic based on current state and input x
      -- State 000: x=0 → 000, x=1 → 001
      -- State 001: x=0 → 001, x=1 → 100
      -- State 010: x=0 → 010, x=1 → 001
      -- State 011: x=0 → 001, x=1 → 010
      -- State 100: x=0 → 011, x=1 → 100
      
      let isA := state === Signal.pure stA
      let isB := state === Signal.pure stB
      let isC := state === Signal.pure stC
      let isD := state === Signal.pure stD
      let isE := state === Signal.pure stE
      
      -- Next state for each current state
      let nextFromA := Signal.mux x (Signal.pure stB) (Signal.pure stA)
      let nextFromB := Signal.mux x (Signal.pure stE) (Signal.pure stB)
      let nextFromC := Signal.mux x (Signal.pure stB) (Signal.pure stC)
      let nextFromD := Signal.mux x (Signal.pure stC) (Signal.pure stB)
      let nextFromE := Signal.mux x (Signal.pure stE) (Signal.pure stD)
      
      -- Select next state based on current state
      let nextState := 
        hw_cond (Signal.pure stA)
          | isA => nextFromA
          | isB => nextFromB
          | isC => nextFromC
          | isD => nextFromD
          | isE => nextFromE
      
      -- Apply synchronous reset: reset → state A (000)
      let nextWithReset := Signal.mux reset (Signal.pure stA) nextState
      
      -- Register with initial value A (000)
      Signal.register stA nextWithReset
  
  -- Output z: 1 for states D (011) and E (100)
  let isDOut := state === Signal.pure stD
  let isEOut := state === Signal.pure stE
  let z_bool := isDOut ||| isEOut
  Signal.mux z_bool (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob121_2014_q3bfsm
