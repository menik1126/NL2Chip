import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Simple test -/
def prob140_test {dom : DomainConfig}
    (a : Signal dom (BitVec 1))
    : Signal dom (BitVec 1) :=
  a

#synthesizeVerilog prob140_test
