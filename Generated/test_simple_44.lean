import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_simple {dom : DomainConfig}
    (a b : Signal dom (BitVec 3))
    : Signal dom (BitVec 3 × BitVec 6) :=
  let not_b : Signal dom (BitVec 3) := ~~~b
  let not_a : Signal dom (BitVec 3) := ~~~a
  let out_not : Signal dom (BitVec 6) := not_b ++ not_a
  bundle2 a out_not

#synthesizeVerilog test_simple
