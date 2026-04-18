import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 8-bit barrel shifter that shifts right by 0-7 positions based on 3-bit control signal -/
def barrel_shifter {dom : DomainConfig}
    (input : Signal dom (BitVec 8)) (ctrl : Signal dom (BitVec 3))
    : Signal dom (BitVec 8) :=
  -- Extract control bits using bitwise operations
  let ctrl2 := (ctrl &&& 4#3) === 4#3  -- bit 2
  let ctrl1 := (ctrl &&& 2#3) === 2#3  -- bit 1
  let ctrl0 := (ctrl &&& 1#3) === 1#3  -- bit 0
  
  -- Stage 1: conditionally shift by 4
  let stage1 := Signal.mux ctrl2 (input >>> 4#8) input
  
  -- Stage 2: conditionally shift by 2
  let stage2 := Signal.mux ctrl1 (stage1 >>> 2#8) stage1
  
  -- Stage 3: conditionally shift by 1
  Signal.mux ctrl0 (stage2 >>> 1#8) stage2

#synthesizeVerilog barrel_shifter
