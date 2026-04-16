import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Simple test - mask lower 4 bits -/
def test_mask {dom : DomainConfig}
    (inp : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  inp &&& (0xF#8)

#synthesizeVerilog test_mask
