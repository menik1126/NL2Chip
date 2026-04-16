import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test OR chain -/
def test_or {dom : DomainConfig}
    (a b c : Signal dom (BitVec 10))
    : Signal dom (BitVec 10) :=
  a ||| b ||| c

#synthesizeVerilog test_or
