import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_concat {dom : DomainConfig}
    (a : Signal dom (BitVec 8)) : Signal dom (BitVec 16) :=
  let b : Signal dom (BitVec 8) := Signal.pure 0xFF#8
  b ++ a

#synthesizeVerilog test_concat
