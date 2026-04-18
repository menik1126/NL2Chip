import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 8-bit barrel shifter that shifts right by 0-7 positions based on 3-bit control signal -/
def barrel_shifter {dom : DomainConfig}
    (inp : Signal dom (BitVec 8)) (ctrl : Signal dom (BitVec 3))
    : Signal dom (BitVec 8) :=
  -- Stage 1: shift by 4 if ctrl[2] is set
  let ctrl2_bit := (ctrl >>> (2 : BitVec 3)) &&& (1 : BitVec 3)
  let ctrl2 := ctrl2_bit === (1 : BitVec 3)
  let shifted4 := inp >>> (4 : BitVec 8)
  let x := Signal.mux ctrl2 shifted4 inp
  
  -- Stage 2: shift by 2 if ctrl[1] is set  
  let ctrl1_bit := (ctrl >>> (1 : BitVec 3)) &&& (1 : BitVec 3)
  let ctrl1 := ctrl1_bit === (1 : BitVec 3)
  let shifted2 := x >>> (2 : BitVec 8)
  let y := Signal.mux ctrl1 shifted2 x
  
  -- Stage 3: shift by 1 if ctrl[0] is set
  let ctrl0_bit := ctrl &&& (1 : BitVec 3)
  let ctrl0 := ctrl0_bit === (1 : BitVec 3)
  let shifted1 := y >>> (1 : BitVec 8)
  Signal.mux ctrl0 shifted1 y

#synthesizeVerilog barrel_shifter
