import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Simple test - extract lower 4 bits -/
def test_extract {dom : DomainConfig}
    (inp : Signal dom (BitVec 8)) : Signal dom (BitVec 4) :=
  Signal.map (fun v => BitVec.cast (by omega) (v &&& 0xF#8)) inp

#synthesizeVerilog test_extract
