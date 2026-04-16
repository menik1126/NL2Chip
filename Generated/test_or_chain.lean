import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test chained OR operation -/
def test_or_chain {dom : DomainConfig}
    (a b c : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  let d : Signal dom Bool := a ||| b ||| c
  Signal.mux d (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog test_or_chain
