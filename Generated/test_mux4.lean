import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test mux with bool condition returning 4-bit -/
def test_mux4 {dom : DomainConfig}
    (state : Signal dom (BitVec 4)) (inp : Signal dom Bool)
    : Signal dom (BitVec 4 × BitVec 1) :=
  let sA : Signal dom Bool := Signal.map (fun x => x.getLsb 0) state
  let nsA_bool : Signal dom Bool := sA &&& (~~~inp)
  let ns_A_bv : Signal dom (BitVec 4) := Signal.mux nsA_bool (Signal.pure 0b0001#4) (Signal.pure 0b0000#4)
  let ns_B_bv : Signal dom (BitVec 4) := Signal.mux inp (Signal.pure 0b0010#4) (Signal.pure 0b0000#4)
  let next_state : Signal dom (BitVec 4) := ns_A_bv ||| ns_B_bv
  let out : Signal dom (BitVec 1) := Signal.mux sA (Signal.pure 1#1) (Signal.pure 0#1)
  bundle2 next_state out

#synthesizeVerilog test_mux4
