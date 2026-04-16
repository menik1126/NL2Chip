import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test OR of two 4-bit signals -/
def test_or4 {dom : DomainConfig}
    (a b : Signal dom (BitVec 4))
    : Signal dom (BitVec 4 × BitVec 4) :=
  let c : Signal dom (BitVec 4) := a ||| b
  bundle2 a c

#synthesizeVerilog test_or4
