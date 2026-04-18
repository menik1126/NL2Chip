import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_simple {dom : DomainConfig}
    (inp : Signal dom (BitVec 16)) (sel : Signal dom Bool)
    : Signal dom (BitVec 4) :=
  let slice0 := Signal.map (fun x => BitVec.extractLsb' 0 4 x) inp
  let slice1 := Signal.map (fun x => BitVec.extractLsb' 4 4 x) inp
  Signal.mux sel slice1 slice0

#synthesizeVerilog test_simple
