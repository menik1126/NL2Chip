import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test Bool tuple -/
def test7 {dom : DomainConfig}
    (a b : Signal dom Bool)
    : Signal dom (Bool × Bool) :=
  bundle2 a b

#synthesizeVerilog test7
