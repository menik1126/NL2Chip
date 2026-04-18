import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_mux4to1 {dom : DomainConfig}
    (inp : Signal dom (BitVec 16)) (sel : Signal dom (BitVec 2))
    : Signal dom (BitVec 4) :=
  let sel0 := Signal.map (fun s => s.getLsb 0) sel
  let sel1 := Signal.map (fun s => s.getLsb 1) sel
  
  let slice0 := Signal.map (fun x => BitVec.extractLsb' 0 4 x) inp
  let slice1 := Signal.map (fun x => BitVec.extractLsb' 4 4 x) inp
  let slice2 := Signal.map (fun x => BitVec.extractLsb' 8 4 x) inp
  let slice3 := Signal.map (fun x => BitVec.extractLsb' 12 4 x) inp
  
  let mux01 := Signal.mux sel0 slice1 slice0
  let mux23 := Signal.mux sel0 slice3 slice2
  
  Signal.mux sel1 mux23 mux01

#synthesizeVerilog test_mux4to1
