import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Extract bits [3:0] -/
def extract0 (inp : BitVec 1024) : BitVec 4 :=
  BitVec.cast (by omega) (inp.extractLsb 3 0)

/-- Extract bits [7:4] -/
def extract1 (inp : BitVec 1024) : BitVec 4 :=
  BitVec.cast (by omega) (inp.extractLsb 7 4)

/-- Extract bits [11:8] -/
def extract2 (inp : BitVec 1024) : BitVec 4 :=
  BitVec.cast (by omega) (inp.extractLsb 11 8)

/-- Extract bits [15:12] -/
def extract3 (inp : BitVec 1024) : BitVec 4 :=
  BitVec.cast (by omega) (inp.extractLsb 15 12)

/-- Test with just 4-to-1 mux -/
def test_mux4to1 {dom : DomainConfig}
    (input : Signal dom (BitVec 1024)) (sel : Signal dom (BitVec 2))
    : Signal dom (BitVec 4) :=
  let sel0 := Signal.map (fun s => s.getLsb 0) sel
  let sel1 := Signal.map (fun s => s.getLsb 1) sel
  
  -- Level 0: 4 inputs -> 2 outputs
  let inp0 := Signal.map extract0 input
  let inp1 := Signal.map extract1 input
  let inp2 := Signal.map extract2 input
  let inp3 := Signal.map extract3 input
  
  let l0_0 := Signal.mux sel0 inp1 inp0
  let l0_1 := Signal.mux sel0 inp3 inp2
  
  -- Level 1: 2 -> 1
  Signal.mux sel1 l0_1 l0_0

#synthesizeVerilog test_mux4to1
