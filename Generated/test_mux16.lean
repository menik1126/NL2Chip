import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_mux16to1 {dom : DomainConfig}
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
  let slice4 := Signal.map (fun x => BitVec.extractLsb' 16 4 x) inp
  let slice5 := Signal.map (fun x => BitVec.extractLsb' 20 4 x) inp
  let slice6 := Signal.map (fun x => BitVec.extractLsb' 24 4 x) inp
  let slice7 := Signal.map (fun x => BitVec.extractLsb' 28 4 x) inp
  let slice8 := Signal.map (fun x => BitVec.extractLsb' 32 4 x) inp
  let slice9 := Signal.map (fun x => BitVec.extractLsb' 36 4 x) inp
  let slice10 := Signal.map (fun x => BitVec.extractLsb' 40 4 x) inp
  let slice11 := Signal.map (fun x => BitVec.extractLsb' 44 4 x) inp
  let slice12 := Signal.map (fun x => BitVec.extractLsb' 48 4 x) inp
  let slice13 := Signal.map (fun x => BitVec.extractLsb' 52 4 x) inp
  let slice14 := Signal.map (fun x => BitVec.extractLsb' 56 4 x) inp
  let slice15 := Signal.map (fun x => BitVec.extractLsb' 60 4 x) inp

  let mux0_0 := Signal.mux sel0 slice1 slice0
  let mux0_1 := Signal.mux sel0 slice3 slice2
  let mux0_2 := Signal.mux sel0 slice5 slice4
  let mux0_3 := Signal.mux sel0 slice7 slice6
  let mux0_4 := Signal.mux sel0 slice9 slice8
  let mux0_5 := Signal.mux sel0 slice11 slice10
  let mux0_6 := Signal.mux sel0 slice13 slice12
  let mux0_7 := Signal.mux sel0 slice15 slice14

  let mux1_0 := Signal.mux sel1 mux0_1 mux0_0
  let mux1_1 := Signal.mux sel1 mux0_3 mux0_2
  let mux1_2 := Signal.mux sel1 mux0_5 mux0_4
  let mux1_3 := Signal.mux sel1 mux0_7 mux0_6

  let mux2_0 := Signal.mux sel2 mux1_1 mux1_0
  let mux2_1 := Signal.mux sel2 mux1_3 mux1_2

  Signal.mux sel3 mux2_1 mux2_0

#synthesizeVerilog test_mux16to1
