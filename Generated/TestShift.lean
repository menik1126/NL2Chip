import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_shift {dom : DomainConfig}
    (inp : Signal dom (BitVec 4)) : Signal dom (BitVec 4) :=
  let shifted := inp <<< 1#4
  shifted

#synthesizeVerilog test_shift
