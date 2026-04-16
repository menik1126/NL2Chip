import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test with parentheses -/
def test_paren {dom : DomainConfig}
    (y : Signal dom (BitVec 3))
    (w : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  let y0 : Signal dom Bool := Signal.map (fun x => x.getLsb 0) y
  let y1 : Signal dom Bool := Signal.map (fun x => x.getLsb 1) y
  
  let temp : Signal dom Bool := y0 &&& y1
  let result : Signal dom Bool := temp &&& w
  
  Signal.mux result (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog test_paren
