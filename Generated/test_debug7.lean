import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test: extracting single bit as BitVec 1
def test7 {dom : DomainConfig}
    (state : Signal dom (BitVec 4))
    : Signal dom (BitVec 4 × BitVec 1) :=
  -- Extract bit 0 as 1-bit BitVec
  let sA_bv : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 0 1) state
  let sA : Signal dom Bool := sA_bv === (Signal.pure 1#1)
  let out : Signal dom (BitVec 1) := Signal.mux sA (Signal.pure 1#1) (Signal.pure 0#1)
  bundle2 state out

#synthesizeVerilog test7
