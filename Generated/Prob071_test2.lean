import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test constant -/
def prob071_test2 {dom : DomainConfig}
    (inp : Signal dom (BitVec 8)) : Signal dom (BitVec 3) :=
  Signal.pure 0#3

#synthesizeVerilog prob071_test2
