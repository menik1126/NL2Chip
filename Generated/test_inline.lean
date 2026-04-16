import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test: Inline boolean operations -/
def test_inline {dom : DomainConfig}
    (inp : Signal dom Bool)
    (state : Signal dom (BitVec 4))
    : Signal dom (BitVec 4 × BitVec 1) :=
  let stateA := Signal.map (fun x => x.getLsb 0) state
  let stateC := Signal.map (fun x => x.getLsb 2) state
  let notIn := ~~~inp
  let nextA := Signal.mux ((stateA ||| stateC) &&& notIn) (Signal.pure 1#1) (Signal.pure 0#1)
  let zero := Signal.pure 0#1
  let result4 := zero ++ zero ++ zero ++ nextA
  let out := zero
  bundle2 result4 out

#synthesizeVerilog test_inline
