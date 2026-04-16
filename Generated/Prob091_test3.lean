import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def prob091_test3 {dom : DomainConfig}
    (y : Signal dom (BitVec 3)) (w : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  -- Extract bit 0 as a 1-bit BitVec
  let y0 : Signal dom (BitVec 1) := Signal.map (BitVec.extractLsb' 0 1) y
  -- Convert w (Bool) to BitVec 1
  let wBit : Signal dom (BitVec 1) := Signal.mux w (Signal.pure 1#1) (Signal.pure 0#1)
  y0 &&& wBit

#synthesizeVerilog prob091_test3
