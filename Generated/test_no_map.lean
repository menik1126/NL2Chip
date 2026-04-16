import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test without Signal.map -/
def test_no_map {dom : DomainConfig}
    (a b : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  let result : Signal dom Bool := a &&& b
  
  Signal.mux result (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog test_no_map
