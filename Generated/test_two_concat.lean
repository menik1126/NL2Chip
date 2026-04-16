import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test: Two concatenations -/
def test_two_concat {dom : DomainConfig}
    (state : Signal dom (BitVec 4))
    : Signal dom (BitVec 4 × BitVec 1) :=
  let stateA := Signal.map (fun x => x.getLsb 0) state
  let nextA := Signal.mux stateA (Signal.pure 1#1) (Signal.pure 0#1)
  let zero := Signal.pure 0#1
  let lo := nextA ++ zero
  let hi := zero ++ zero
  let result4 := hi ++ lo
  let out := zero
  bundle2 result4 out

#synthesizeVerilog test_two_concat
