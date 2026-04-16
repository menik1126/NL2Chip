import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_bundle3_small {dom : DomainConfig}
    (input : Signal dom (BitVec 8))
    : Signal dom (BitVec 8 × BitVec 8 × BitVec 8) :=
  let a : Signal dom (BitVec 8) := input >>> 1#8
  let b : Signal dom (BitVec 8) := input <<< 1#8
  let c : Signal dom (BitVec 8) := input ^^^ (1#8 : BitVec 8)
  bundle3 a b c

#synthesizeVerilog test_bundle3_small
