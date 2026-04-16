import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding
private abbrev stA : BitVec 2 := 0#2
private abbrev stB : BitVec 2 := 1#2
private abbrev stC : BitVec 2 := 2#2
private abbrev stD : BitVec 2 := 3#2

/-- FSM arbiter with priority: r[0] > r[1] > r[2].
    Active-low synchronous reset to state A. -/
def prob148_2013_q2afsm {dom : DomainConfig}
    (resetn : Signal dom Bool)
    (r : Signal dom (BitVec 3))
    : Signal dom (BitVec 3) :=
  -- Extract individual request bits
  let r0 := Signal.map (fun x => x.getLsb 0) r
  let r1 := Signal.map (fun x => x.getLsb 1) r
  let r2 := Signal.map (fun x => x.getLsb 2) r
  
  -- State machine with feedback
  let state := Signal.loop fun (state : Signal dom (BitVec 2)) =>
    -- Check which state we're in
    let isA := state === Signal.pure stA
    let isB := state === Signal.pure stB
    let isC := state === Signal.pure stC
    
    -- Next state from A: priority r0 > r1 > r2
    let nextFromA := Signal.mux r0 (Signal.pure stB)
                      (Signal.mux r1 (Signal.pure stC)
                        (Signal.mux r2 (Signal.pure stD) (Signal.pure stA)))
    
    -- Next state from B: stay if r0=1, else go to A
    let nextFromB := Signal.mux r0 (Signal.pure stB) (Signal.pure stA)
    
    -- Next state from C: stay if r1=1, else go to A
    let nextFromC := Signal.mux r1 (Signal.pure stC) (Signal.pure stA)
    
    -- Next state from D: stay if r2=1, else go to A
    let nextFromD := Signal.mux r2 (Signal.pure stD) (Signal.pure stA)
    
    -- Select next state based on current state
    let nextState := Signal.mux isA nextFromA
                      (Signal.mux isB nextFromB
                        (Signal.mux isC nextFromC nextFromD))
    
    -- Apply active-low reset: resetn=0 → A
    let nextWithReset := Signal.mux resetn nextState (Signal.pure stA)
    
    -- Register with initial value A
    Signal.register stA nextWithReset
  
  -- Generate outputs: g[0]=1 in B, g[1]=1 in C, g[2]=1 in D
  let outIsB := state === Signal.pure stB
  let outIsC := state === Signal.pure stC
  let outIsD := state === Signal.pure stD
  
  -- Build output using mux: if B then 001, else if C then 010, else if D then 100, else 000
  Signal.mux outIsB (Signal.pure 1#3)
    (Signal.mux outIsC (Signal.pure 2#3)
      (Signal.mux outIsD (Signal.pure 4#3) (Signal.pure 0#3)))


