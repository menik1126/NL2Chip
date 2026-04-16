import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Simple test with bundle3 -/
def prob140_test2 {dom : DomainConfig}
    (a : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1 × BitVec 1) :=
  bundle3 a a a

#synthesizeVerilog prob140_test2
