import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def extract0 (inp : BitVec 1024) : BitVec 4 :=
  BitVec.cast (by omega) (inp.extractLsb 3 0)

/-- Simple test with Signal.map -/
def test_with_map {dom : DomainConfig}
    (input : Signal dom (BitVec 1024)) (sel : Signal dom Bool)
    : Signal dom (BitVec 4) :=
  let inp0 := Signal.map extract0 input
  Signal.mux sel inp0 inp0

#synthesizeVerilog test_with_map
