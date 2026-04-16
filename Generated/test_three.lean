import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test 3 outputs -/
def test_three {dom : DomainConfig}
    (a b c : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1 × BitVec 1) :=
  bundle3 a b c

#synthesizeVerilog test_three
