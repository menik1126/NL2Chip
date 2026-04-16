import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test mux4 simple -/
def test_mux4b {dom : DomainConfig}
    (state : Signal dom (BitVec 4)) (inp : Signal dom Bool)
    : Signal dom (BitVec 4 × BitVec 1) :=
  let sA : Signal dom Bool := Signal.map (fun x => x.getLsb 0) state
  let ns_A_bv : Signal dom (BitVec 4) := Signal.mux sA (Signal.pure 0b0001#4) (Signal.pure 0b0000#4)
  let out : Signal dom (BitVec 1) := Signal.mux sA (Signal.pure 1#1) (Signal.pure 0#1)
  bundle2 ns_A_bv out

#synthesizeVerilog test_mux4b
