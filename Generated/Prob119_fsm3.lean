import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding
private abbrev stA : BitVec 2 := 0#2
private abbrev stB : BitVec 2 := 1#2
private abbrev stC : BitVec 2 := 2#2
private abbrev stD : BitVec 2 := 3#2

/-- Moore FSM with 4 states implementing the given state transition table.
    State A: in=0→A, in=1→B, out=0
    State B: in=0→C, in=1→B, out=0
    State C: in=0→A, in=1→D, out=0
    State D: in=0→C, in=1→B, out=1
    Async reset to state A. -/
def prob119_fsm3 {dom : DomainConfig}
    (areset : Signal dom Bool)
    (inp : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  let state := Signal.loop fun (state : Signal dom (BitVec 2)) =>
    -- Next state logic based on current state and input
    let isA := state === Signal.pure stA
    let isB := state === Signal.pure stB
    let isC := state === Signal.pure stC
    let isD := state === Signal.pure stD
    
    -- Compute next state for each current state
    -- A: in=0→A, in=1→B
    let nextFromA := Signal.mux inp (Signal.pure stB) (Signal.pure stA)
    -- B: in=0→C, in=1→B
    let nextFromB := Signal.mux inp (Signal.pure stB) (Signal.pure stC)
    -- C: in=0→A, in=1→D
    let nextFromC := Signal.mux inp (Signal.pure stD) (Signal.pure stA)
    -- D: in=0→C, in=1→B
    let nextFromD := Signal.mux inp (Signal.pure stB) (Signal.pure stC)
    
    -- Select next state based on current state
    let nextState := 
      Signal.mux isA nextFromA
        (Signal.mux isB nextFromB
          (Signal.mux isC nextFromC nextFromD))
    
    -- Apply async reset (modeled as sync): areset → A
    let nextWithReset := Signal.mux areset (Signal.pure stA) nextState
    
    -- Register with initial value A (matches areset behavior)
    Signal.register stA nextWithReset
  
  -- Output: 1 when state == D, 0 otherwise
  let outBool := state === Signal.pure stD
  Signal.mux outBool (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob119_fsm3
