import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_mux16to1_nosynth {dom : DomainConfig}
    (inp : Signal dom (BitVec 64)) (sel : Signal dom (BitVec 4))
    : Signal dom (BitVec 4) :=
  let sel0 := Signal.map (fun s => s.getLsb 0) sel
  let sel1 := Signal.map (fun s => s.getLsb 1) sel
  let sel2 := Signal.map (fun s => s.getLsb 2) sel
  let sel3 := Signal.map (fun s => s.getLsb 3) sel

  let slice0 := Signal.map (fun x => BitVec.extractLsb' 0 4 x) inp
  let slice1 := Signal.map (fun x => BitVec.extractLsb' 4 4 x) inp
  let slice2 := Signal.map (fun x => BitVec.extractLsb' 8 4 x) inp
  let slice3 := Signal.map (fun x => BitVec.extractLsb' 12 4 x) inp

  let mux0_0 := Signal.mux sel0 slice1 slice0
  let mux0_1 := Signal.mux sel0 slice3 slice2

  let mux1_0 := Signal.mux sel1 mux0_1 mux0_0

  Signal.mux sel2 mux1_0 mux1_0
