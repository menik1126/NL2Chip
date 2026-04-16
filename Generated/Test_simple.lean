import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Simple test -/
def test_simple {dom : DomainConfig}
    (input : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  input

#synthesizeVerilog test_simple
