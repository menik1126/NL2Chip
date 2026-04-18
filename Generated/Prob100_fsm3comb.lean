import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding
private abbrev stA : BitVec 2 := 0#2
private abbrev stB : BitVec 2 := 1#2
private abbrev stC : BitVec 2 := 2#2
private abbrev stD : BitVec 2 := 3#2

/-- FSM combinational logic: computes next_state and output based on current state and input -/
def prob100_fsm3comb {dom : DomainConfig}
    (inp : Signal dom Bool)
    (state : Signal dom (BitVec 2))
    : Signal dom (BitVec 2 × BitVec 1) :=
  -- Check which state we're in
  let isA := state === Signal.pure stA
  let isB := state === Signal.pure stB
  let isC := state === Signal.pure stC
  let isD := state === Signal.pure stD
  
  -- Next state logic based on state transition table:
  -- A: in=0 → A, in=1 → B
  -- B: in=0 → C, in=1 → B
  -- C: in=0 → A, in=1 → D
  -- D: in=0 → C, in=1 → B
  
  let nextFromA := Signal.mux inp (Signal.pure stB) (Signal.pure stA)
  let nextFromB := Signal.mux inp (Signal.pure stB) (Signal.pure stC)
  let nextFromC := Signal.mux inp (Signal.pure stD) (Signal.pure stA)
  let nextFromD := Signal.mux inp (Signal.pure stB) (Signal.pure stC)
  
  -- Priority mux to select next state based on current state
  let next_state := Signal.mux isA nextFromA
    (Signal.mux isB nextFromB
      (Signal.mux isC nextFromC nextFromD))
  
  -- Output logic: out = 1 when state == D, else 0
  let out := Signal.mux isD (Signal.pure 1#1) (Signal.pure 0#1)
  
  bundle2 next_state out

#synthesizeVerilog prob100_fsm3comb
