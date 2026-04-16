import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

private abbrev stA : BitVec 2 := 0#2
private abbrev stB : BitVec 2 := 1#2
private abbrev stC : BitVec 2 := 2#2
private abbrev stD : BitVec 2 := 3#2

def prob148_2013_q2afsm_spec {dom : DomainConfig}
    (resetn : Signal dom Bool)
    (r : Signal dom (BitVec 3))
    : Signal dom (BitVec 3) :=
  let state : Signal dom (BitVec 2) :=
    Signal.loop fun (st : Signal dom (BitVec 2)) =>
      let r0 : Signal dom Bool := (r &&& (Signal.pure (1#3 : BitVec 3) : Signal dom (BitVec 3))) === (Signal.pure 1#3)
      let r1 : Signal dom Bool := ((r >>> (1#3 : BitVec 3)) &&& (Signal.pure (1#3 : BitVec 3) : Signal dom (BitVec 3))) === (Signal.pure 1#3)
      let r2 : Signal dom Bool := ((r >>> (2#3 : BitVec 3)) &&& (Signal.pure (1#3 : BitVec 3) : Signal dom (BitVec 3))) === (Signal.pure 1#3)
      
      let isA := st === (Signal.pure stA)
      let isB := st === (Signal.pure stB)
      let isC := st === (Signal.pure stC)
      
      let nextState := 
        Signal.mux isA 
          (Signal.mux r0 (Signal.pure stB)
            (Signal.mux r1 (Signal.pure stC)
              (Signal.mux r2 (Signal.pure stD) (Signal.pure stA))))
          (Signal.mux isB 
            (Signal.mux r0 (Signal.pure stB) (Signal.pure stA))
            (Signal.mux isC 
              (Signal.mux r1 (Signal.pure stC) (Signal.pure stA))
              (Signal.mux r2 (Signal.pure stD) (Signal.pure stA))))
      
      Signal.register stA (Signal.mux (~~~resetn) (Signal.pure stA) nextState)
  
  let g0_bool := state === (Signal.pure stB)
  let g1_bool := state === (Signal.pure stC)
  let g2_bool := state === (Signal.pure stD)
  
  let g0 : Signal dom (BitVec 3) := Signal.mux g0_bool (Signal.pure 1#3) (Signal.pure 0#3)
  let g1 : Signal dom (BitVec 3) := Signal.mux g1_bool (Signal.pure 2#3) (Signal.pure 0#3)
  let g2 : Signal dom (BitVec 3) := Signal.mux g2_bool (Signal.pure 4#3) (Signal.pure 0#3)
  
  g0 ||| g1 ||| g2

-- Optimized: direct bit manipulation on state
def prob148_2013_q2afsm {dom : DomainConfig}
    (resetn : Signal dom Bool)
    (r : Signal dom (BitVec 3))
    : Signal dom (BitVec 3) :=
  let state : Signal dom (BitVec 2) :=
    Signal.loop fun (st : Signal dom (BitVec 2)) =>
      let r0 : Signal dom Bool := (r &&& (Signal.pure (1#3 : BitVec 3) : Signal dom (BitVec 3))) === (Signal.pure 1#3)
      let r1 : Signal dom Bool := ((r >>> (1#3 : BitVec 3)) &&& (Signal.pure (1#3 : BitVec 3) : Signal dom (BitVec 3))) === (Signal.pure 1#3)
      let r2 : Signal dom Bool := ((r >>> (2#3 : BitVec 3)) &&& (Signal.pure (1#3 : BitVec 3) : Signal dom (BitVec 3))) === (Signal.pure 1#3)
      
      let isA := st === (Signal.pure stA)
      let isB := st === (Signal.pure stB)
      let isC := st === (Signal.pure stC)
      
      let nextState := 
        Signal.mux isA 
          (Signal.mux r0 (Signal.pure stB)
            (Signal.mux r1 (Signal.pure stC)
              (Signal.mux r2 (Signal.pure stD) (Signal.pure stA))))
          (Signal.mux isB 
            (Signal.mux r0 (Signal.pure stB) (Signal.pure stA))
            (Signal.mux isC 
              (Signal.mux r1 (Signal.pure stC) (Signal.pure stA))
              (Signal.mux r2 (Signal.pure stD) (Signal.pure stA))))
      
      Signal.register stA (Signal.mux (~~~resetn) (Signal.pure stA) nextState)
  
  -- Optimized output: directly manipulate state bits
  -- Extend state from 2 bits to 3 bits by padding with 0
  let state_3bit : Signal dom (BitVec 3) := 
    Signal.map (fun (s : BitVec 2) => s.zeroExtend 3) state
  
  -- Extract individual bits
  let s0_3 : Signal dom (BitVec 3) := state_3bit &&& (Signal.pure (1#3 : BitVec 3) : Signal dom (BitVec 3))
  let s1_3 : Signal dom (BitVec 3) := (state_3bit >>> (1#3 : BitVec 3)) &&& (Signal.pure (1#3 : BitVec 3) : Signal dom (BitVec 3))
  
  -- Compute output bits: g[0] = s0 & ~s1, g[1] = ~s0 & s1, g[2] = s0 & s1
  let g0 : Signal dom (BitVec 3) := s0_3 &&& (~~~s1_3)
  let g1 : Signal dom (BitVec 3) := ((~~~s0_3) &&& s1_3) <<< (1#3 : BitVec 3)
  let g2 : Signal dom (BitVec 3) := (s0_3 &&& s1_3) <<< (2#3 : BitVec 3)
  
  g0 ||| g1 ||| g2

#synthesizeVerilog prob148_2013_q2afsm
