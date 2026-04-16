import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_bundle3_minimal {dom : DomainConfig}
    (a b : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1 × BitVec 1) :=
  bundle3 a b a

#synthesizeVerilog test_bundle3_minimal
