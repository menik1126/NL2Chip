import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_nested_bundle {dom : DomainConfig}
    (a : Signal dom (BitVec 10))
    (b : Signal dom (BitVec 1))
    (c : Signal dom (BitVec 1))
    : Signal dom ((BitVec 10 × BitVec 1) × BitVec 1) :=
  let ab := bundle2 a b
  bundle2 ab c

#synthesizeVerilog test_nested_bundle
