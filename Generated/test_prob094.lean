import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_prob094 {dom : DomainConfig}
    (input : Signal dom (BitVec 4))
    : Signal dom (BitVec 4 × BitVec 4 × BitVec 4) :=
  let a := input >>> 1#4
  let b := input <<< 1#4
  let c := input ^^^ (1#4 : BitVec 4)
  bundle3 a b c

#synthesizeVerilog test_prob094
