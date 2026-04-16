import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Simple 8-to-1 mux test -/
def test_mux {dom : DomainConfig}
    (inp : Signal dom (BitVec 32)) (sel : Signal dom (BitVec 3))
    : Signal dom (BitVec 4) :=
  let sel0 := Signal.map (fun s => s.getLsb 0) sel
  let sel1 := Signal.map (fun s => s.getLsb 1) sel
  let sel2 := Signal.map (fun s => s.getLsb 2) sel
  
  -- Extract each 4-bit chunk using shift and truncate
  let chunk0 := Signal.map (fun v => v.truncate 4) inp  -- bits [3:0]
  let chunk1 := Signal.map (fun v => (v >>> 4#32).truncate 4) inp  -- bits [7:4]
  let chunk2 := Signal.map (fun v => (v >>> 8#32).truncate 4) inp  -- bits [11:8]
  let chunk3 := Signal.map (fun v => (v >>> 12#32).truncate 4) inp  -- bits [15:12]
  let chunk4 := Signal.map (fun v => (v >>> 16#32).truncate 4) inp  -- bits [19:16]
  let chunk5 := Signal.map (fun v => (v >>> 20#32).truncate 4) inp  -- bits [23:20]
  let chunk6 := Signal.map (fun v => (v >>> 24#32).truncate 4) inp  -- bits [27:24]
  let chunk7 := Signal.map (fun v => (v >>> 28#32).truncate 4) inp  -- bits [31:28]
  
  -- Level 0: 4 muxes
  let m0_0 := Signal.mux sel0 chunk1 chunk0
  let m0_1 := Signal.mux sel0 chunk3 chunk2
  let m0_2 := Signal.mux sel0 chunk5 chunk4
  let m0_3 := Signal.mux sel0 chunk7 chunk6
  
  -- Level 1: 2 muxes
  let m1_0 := Signal.mux sel1 m0_1 m0_0
  let m1_1 := Signal.mux sel1 m0_3 m0_2
  
  -- Level 2: final mux
  Signal.mux sel2 m1_1 m1_0

#synthesizeVerilog test_mux
