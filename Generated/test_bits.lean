import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_bits {dom : DomainConfig}
    (r : Signal dom (BitVec 3))
    : Signal dom (BitVec 3) :=
  let r0 : Signal dom (BitVec 1) := Signal.map (fun x => BitVec.extractLsb' 0 1 x) r
  let r1 : Signal dom (BitVec 1) := Signal.map (fun x => BitVec.extractLsb' 1 1 x) r
  let r2 : Signal dom (BitVec 1) := Signal.map (fun x => BitVec.extractLsb' 2 1 x) r
  let r0b : Signal dom Bool := r0 === Signal.pure 1#1
  let r1b : Signal dom Bool := r1 === Signal.pure 1#1
  let r2b : Signal dom Bool := r2 === Signal.pure 1#1
  Signal.mux r0b (Signal.pure 1#3)
    (Signal.mux r1b (Signal.pure 2#3)
      (Signal.mux r2b (Signal.pure 4#3) (Signal.pure 0#3)))

#synthesizeVerilog test_bits
