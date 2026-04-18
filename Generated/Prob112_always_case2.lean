import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 4-bit priority encoder: outputs position of first 1 bit (LSB has priority). -/
def prob112_always_case2 {dom : DomainConfig}
    (input : Signal dom (BitVec 4)) : Signal dom (BitVec 2) :=
  let bit0 := (input &&& 1#4) === 1#4
  let bit1 := (input &&& 2#4) === 2#4
  let bit2 := (input &&& 4#4) === 4#4
  let bit3 := (input &&& 8#4) === 8#4
  -- Priority: bit0 > bit1 > bit2 > bit3
  let level3 := Signal.mux bit3 (Signal.pure 3#2) (Signal.pure 0#2)
  let level2 := Signal.mux bit2 (Signal.pure 2#2) level3
  let level1 := Signal.mux bit1 (Signal.pure 1#2) level2
  let level0 := Signal.mux bit0 (Signal.pure 0#2) level1
  level0

#synthesizeVerilog prob112_always_case2
