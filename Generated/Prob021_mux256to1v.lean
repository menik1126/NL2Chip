import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Extract 4-bit slice at position i from 1024-bit input -/
@[inline] def getSlice (i : Nat) (inp : BitVec 1024) : BitVec 4 :=
  BitVec.extractLsb' (i * 4) 4 inp

/-- 4-bit wide, 256-to-1 multiplexer. Selects 4 bits from 1024-bit input based on 8-bit selector. -/
def prob021_mux256to1v {dom : DomainConfig}
    (inp : Signal dom (BitVec 1024)) (sel : Signal dom (BitVec 8))
    : Signal dom (BitVec 4) :=
  -- Extract sel bits
  let sel0 := Signal.map (fun s => s.getLsb 0) sel
  let sel1 := Signal.map (fun s => s.getLsb 1) sel
  let sel2 := Signal.map (fun s => s.getLsb 2) sel
  let sel3 := Signal.map (fun s => s.getLsb 3) sel
  let sel4 := Signal.map (fun s => s.getLsb 4) sel
  let sel5 := Signal.map (fun s => s.getLsb 5) sel
  let sel6 := Signal.map (fun s => s.getLsb 6) sel
  let sel7 := Signal.map (fun s => s.getLsb 7) sel
  
  -- Helper to get a slice signal
  let slice (i : Nat) : Signal dom (BitVec 4) := Signal.map (getSlice i) inp
  
  -- Build mux tree recursively
  let mux2 (i : Nat) := Signal.mux sel0 (slice (2*i+1)) (slice (2*i))
  let mux4 (i : Nat) := Signal.mux sel1 (mux2 (2*i+1)) (mux2 (2*i))
  let mux8 (i : Nat) := Signal.mux sel2 (mux4 (2*i+1)) (mux4 (2*i))
  let mux16 (i : Nat) := Signal.mux sel3 (mux8 (2*i+1)) (mux8 (2*i))
  let mux32 (i : Nat) := Signal.mux sel4 (mux16 (2*i+1)) (mux16 (2*i))
  let mux64 (i : Nat) := Signal.mux sel5 (mux32 (2*i+1)) (mux32 (2*i))
  let mux128 (i : Nat) := Signal.mux sel6 (mux64 (2*i+1)) (mux64 (2*i))
  
  Signal.mux sel7 (mux128 1) (mux128 0)

#synthesizeVerilog prob021_mux256to1v
