import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_simple7 {dom : DomainConfig}
    (inp : Signal dom (BitVec 8)) : Signal dom (BitVec 1) :=
  let state : Signal dom (BitVec 1) :=
    Signal.loop fun (state : Signal dom (BitVec 1)) =>
      -- Extract bit 3 using shift and mask: (inp >>> 3) &&& 1
      let bit3_8bit : Signal dom (BitVec 8) := inp >>> 3#8
      let mask : Signal dom (BitVec 8) := Signal.pure 8#8
      let bit3_masked : Signal dom (BitVec 8) := bit3_8bit &&& mask
      let in3 : Signal dom Bool := bit3_masked === (Signal.pure 8#8)
      let next : Signal dom (BitVec 1) := Signal.mux in3 (Signal.pure 1#1) (Signal.pure 0#1)
      Signal.register 0#1 next
  let out : Signal dom (BitVec 1) := Signal.mux (state === (Signal.pure 1#1)) (Signal.pure 1#1) (Signal.pure 0#1)
  out

#synthesizeVerilog test_simple7
