import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test minimal bundle3 -/
def test_minimal_bundle3 {dom : DomainConfig}
    (a b c : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1 × BitVec 1) :=
  bundle3 a b c

#synthesizeVerilog test_minimal_bundle3
