import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding: A=0, B=1, C=2, D=3
private abbrev stA : BitVec 2 := 0#2
private abbrev stB : BitVec 2 := 1#2
private abbrev stC : BitVec 2 := 2#2
private abbrev stD : BitVec 2 := 3#2

/-- Arbiter FSM with 4 states (A/B/C/D), active-low synchronous reset.
    State B grants device 0 (g[0]=1), C grants device 1 (g[1]=1),
    D grants device 2 (g[2]=1). Priority: device 0 > 1 > 2. -/
def prob148_2013_q2afsm {dom : DomainConfig}
    (resetn : Signal dom Bool)
    (r : Signal dom (BitVec 3))
    : Signal dom (BitVec 3) :=
  -- Extract individual request bits as BitVec 1, then convert to Bool
  let r0bv : Signal dom (BitVec 1) := Signal.map (fun x => BitVec.extractLsb' 0 1 x) r
  let r1bv : Signal dom (BitVec 1) := Signal.map (fun x => BitVec.extractLsb' 1 1 x) r
  let r2bv : Signal dom (BitVec 1) := Signal.map (fun x => BitVec.extractLsb' 2 1 x) r
  let r0 : Signal dom Bool := r0bv === Signal.pure 1#1
  let r1 : Signal dom Bool := r1bv === Signal.pure 1#1
  let r2 : Signal dom Bool := r2bv === Signal.pure 1#1
  -- FSM state loop
  let state : Signal dom (BitVec 2) :=
    Signal.loop fun (state : Signal dom (BitVec 2)) =>
      let isA := state === Signal.pure stA
      let isB := state === Signal.pure stB
      let isC := state === Signal.pure stC
      -- Next state from A: priority r0 > r1 > r2, else stay A
      let nextFromA :=
        Signal.mux r0 (Signal.pure stB)
          (Signal.mux r1 (Signal.pure stC)
            (Signal.mux r2 (Signal.pure stD) (Signal.pure stA)))
      -- Next state from B: stay B if r0=1, else go to A
      let nextFromB := Signal.mux r0 (Signal.pure stB) (Signal.pure stA)
      -- Next state from C: stay C if r1=1, else go to A
      let nextFromC := Signal.mux r1 (Signal.pure stC) (Signal.pure stA)
      -- Next state from D: stay D if r2=1, else go to A
      let nextFromD := Signal.mux r2 (Signal.pure stD) (Signal.pure stA)
      -- Select next state based on current state
      let nextState :=
        Signal.mux isA nextFromA
          (Signal.mux isB nextFromB
            (Signal.mux isC nextFromC nextFromD))
      -- Apply active-low synchronous reset: resetn=0 → go to A
      let nextWithReset := Signal.mux resetn nextState (Signal.pure stA)
      Signal.register stA nextWithReset
  -- Output logic: build 3-bit g using nested Signal.mux with 3-bit constants
  -- g[0]=1 iff state==B => g=3'b001=1
  -- g[1]=1 iff state==C => g=3'b010=2
  -- g[2]=1 iff state==D => g=3'b100=4
  -- else state==A => g=3'b000=0
  let isB_out : Signal dom Bool := state === Signal.pure stB
  let isC_out : Signal dom Bool := state === Signal.pure stC
  let isD_out : Signal dom Bool := state === Signal.pure stD
  Signal.mux isD_out (Signal.pure 4#3)
    (Signal.mux isC_out (Signal.pure 2#3)
      (Signal.mux isB_out (Signal.pure 1#3) (Signal.pure 0#3)))

#synthesizeVerilog prob148_2013_q2afsm
