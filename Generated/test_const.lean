import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test constant -/
def test_const {dom : DomainConfig}
    (input : Signal dom (BitVec 8)) : Signal dom (BitVec 3) :=
  Signal.pure 5#3

#synthesizeVerilog test_const
