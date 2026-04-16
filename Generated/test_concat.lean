import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test: Building BitVec 4 from concatenation -/
def test_concat {dom : DomainConfig}
    (a b c d : Signal dom (BitVec 1))
    : Signal dom (BitVec 4) :=
  let lo := b ++ a
  let hi := d ++ c
  hi ++ lo

#synthesizeVerilog test_concat
