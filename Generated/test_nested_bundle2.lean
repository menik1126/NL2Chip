import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test nested bundle2 for 3 outputs -/
def test_nested_bundle2 {dom : DomainConfig}
    (a b c : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × (BitVec 1 × BitVec 1)) :=
  bundle2 a (bundle2 b c)

#synthesizeVerilog test_nested_bundle2
