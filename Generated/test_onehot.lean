import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Binary encoding (original)
private abbrev stA_bin : BitVec 2 := 0#2
private abbrev stB_bin : BitVec 2 := 1#2
private abbrev stC_bin : BitVec 2 := 2#2
private abbrev stD_bin : BitVec 2 := 3#2

-- One-hot encoding (optimized)
private abbrev stA_oh : BitVec 4 := 0b0001#4
private abbrev stB_oh : BitVec 4 := 0b0010#4
private abbrev stC_oh : BitVec 4 := 0b0100#4
private abbrev stD_oh : BitVec 4 := 0b1000#4

def prob148_2013_q2afsm_spec {dom : DomainConfig}
    (resetn : Signal dom Bool)
    (r : Signal dom (BitVec 3))
    : Signal dom (BitVec 3) :=
  let state : Signal dom (BitVec 2) :=
    Signal.loop fun (st : Signal dom (BitVec 2)) =>
      let r0 : Signal dom Bool := (r &&& (Signal.pure (1#3 : BitVec 3) : Signal dom (BitVec 3))) === (Signal.pure 1#3)
      let r1 : Signal dom Bool := ((r >>> (1#3 : BitVec 3)) &&& (Signal.pure (1#3 : BitVec 3) : Signal dom (BitVec 3))) === (Signal.pure 1#3)
      let r2 : Signal dom Bool := ((r >>> (2#3 : BitVec 3)) &&& (Signal.pure (1#3 : BitVec 3) : Signal dom (BitVec 3))) === (Signal.pure 1#3)
      
      let isA := st === (Signal.pure stA_bin)
      let isB := st === (Signal.pure stB_bin)
      let isC := st === (Signal.pure stC_bin)
      
      let nextState := 
        Signal.mux isA 
          (Signal.mux r0 (Signal.pure stB_bin)
            (Signal.mux r1 (Signal.pure stC_bin)
              (Signal.mux r2 (Signal.pure stD_bin) (Signal.pure stA_bin))))
          (Signal.mux isB 
            (Signal.mux r0 (Signal.pure stB_bin) (Signal.pure stA_bin))
            (Signal.mux isC 
              (Signal.mux r1 (Signal.pure stC_bin) (Signal.pure stA_bin))
              (Signal.mux r2 (Signal.pure stD_bin) (Signal.pure stA_bin))))
      
      Signal.register stA_bin (Signal.mux (~~~resetn) (Signal.pure stA_bin) nextState)
  
  let g0_bool := state === (Signal.pure stB_bin)
  let g1_bool := state === (Signal.pure stC_bin)
  let g2_bool := state === (Signal.pure stD_bin)
  
  let g0 : Signal dom (BitVec 3) := Signal.mux g0_bool (Signal.pure 1#3) (Signal.pure 0#3)
  let g1 : Signal dom (BitVec 3) := Signal.mux g1_bool (Signal.pure 2#3) (Signal.pure 0#3)
  let g2 : Signal dom (BitVec 3) := Signal.mux g2_bool (Signal.pure 4#3) (Signal.pure 0#3)
  
  g0 ||| g1 ||| g2

-- One-hot encoded version
def prob148_2013_q2afsm {dom : DomainConfig}
    (resetn : Signal dom Bool)
    (r : Signal dom (BitVec 3))
    : Signal dom (BitVec 3) :=
  let state : Signal dom (BitVec 4) :=
    Signal.loop fun (st : Signal dom (BitVec 4)) =>
      let r0 : Signal dom Bool := (r &&& (Signal.pure (1#3 : BitVec 3) : Signal dom (BitVec 3))) === (Signal.pure 1#3)
      let r1 : Signal dom Bool := ((r >>> (1#3 : BitVec 3)) &&& (Signal.pure (1#3 : BitVec 3) : Signal dom (BitVec 3))) === (Signal.pure 1#3)
      let r2 : Signal dom Bool := ((r >>> (2#3 : BitVec 3)) &&& (Signal.pure (1#3 : BitVec 3) : Signal dom (BitVec 3))) === (Signal.pure 1#3)
      
      -- One-hot state checks: just check the corresponding bit
      let isA : Signal dom Bool := Signal.map (fun s => s.getLsb 0) st
      let isB : Signal dom Bool := Signal.map (fun s => s.getLsb 1) st
      let isC : Signal dom Bool := Signal.map (fun s => s.getLsb 2) st
      let isD : Signal dom Bool := Signal.map (fun s => s.getLsb 3) st
      
      let nextState := 
        Signal.mux isA 
          (Signal.mux r0 (Signal.pure stB_oh)
            (Signal.mux r1 (Signal.pure stC_oh)
              (Signal.mux r2 (Signal.pure stD_oh) (Signal.pure stA_oh))))
          (Signal.mux isB 
            (Signal.mux r0 (Signal.pure stB_oh) (Signal.pure stA_oh))
            (Signal.mux isC 
              (Signal.mux r1 (Signal.pure stC_oh) (Signal.pure stA_oh))
              (Signal.mux isD
                (Signal.mux r2 (Signal.pure stD_oh) (Signal.pure stA_oh))
                (Signal.pure stA_oh))))  -- default to A for safety
      
      Signal.register stA_oh (Signal.mux (~~~resetn) (Signal.pure stA_oh) nextState)
  
  -- Output is simply bits [3:1] of the one-hot state
  Signal.map (fun s : BitVec 4 => (s >>> 1).truncate 3) state

#synthesizeVerilog prob148_2013_q2afsm
