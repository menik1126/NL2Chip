import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 4-bit priority encoder: outputs the position of the lowest set bit.
    Returns 0 if no bit is set. -/
def prob112_always_case2 {dom : DomainConfig}
    (in_ : Signal dom (BitVec 4)) : Signal dom (BitVec 2) :=
  -- Extract individual bits as BitVec 1 signals
  let b0 : Signal dom (BitVec 1) := Signal.map (BitVec.extractLsb' 0 1 ·) in_
  let b1 : Signal dom (BitVec 1) := Signal.map (BitVec.extractLsb' 1 1 ·) in_
  let b2 : Signal dom (BitVec 1) := Signal.map (BitVec.extractLsb' 2 1 ·) in_
  let b3 : Signal dom (BitVec 1) := Signal.map (BitVec.extractLsb' 3 1 ·) in_
  -- Convert to Bool by comparing with 1
  let c0 : Signal dom Bool := b0 === Signal.pure 1#1
  let c1 : Signal dom Bool := b1 === Signal.pure 1#1
  let c2 : Signal dom Bool := b2 === Signal.pure 1#1
  let c3 : Signal dom Bool := b3 === Signal.pure 1#1
  -- Priority: bit 0 > bit 1 > bit 2 > bit 3 > none
  let pos3   := Signal.mux c3 (Signal.pure 3#2) (Signal.pure 0#2)
  let pos23  := Signal.mux c2 (Signal.pure 2#2) pos3
  let pos123 := Signal.mux c1 (Signal.pure 1#2) pos23
  Signal.mux c0 (Signal.pure 0#2) pos123

#synthesizeVerilog prob112_always_case2
