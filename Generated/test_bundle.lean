import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_bundle {dom : DomainConfig}
    (a b c d : Signal dom (BitVec 1))
    : Signal dom ((BitVec 1 × BitVec 1) × (BitVec 1 × BitVec 1)) :=
  bundle2 (bundle2 a b) (bundle2 c d)

#synthesizeVerilog test_bundle
