import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test without NOT -/
def test_no_not {dom : DomainConfig}
    (y : Signal dom (BitVec 3))
    (w : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  let y0 : Signal dom Bool := Signal.map (fun x => x.getLsb 0) y
  let y1 : Signal dom Bool := Signal.map (fun x => x.getLsb 1) y
  
  let result : Signal dom Bool := y0 &&& y1 &&& w
  
  Signal.mux result (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog test_no_not
