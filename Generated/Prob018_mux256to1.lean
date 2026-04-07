import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 256-to-1 multiplexer: sel selects bit from 256-bit input vector -/
def prob018_mux256to1 {dom : DomainConfig}
    (in_vec : Signal dom (BitVec 256)) (sel : Signal dom (BitVec 8))
    : Signal dom (BitVec 1) :=
  -- Build a tree of muxes, one level for each bit of sel
  -- sel[7] selects between upper and lower 128 bits
  -- sel[6] selects between upper and lower 64 bits of the chosen 128
  -- etc.
  
  -- Level 7: 256 -> 128 bits
  let sel7 := (sel >>> 7#8) &&& 1#8 === 1#8
  let lower128 := Signal.map (BitVec.extractLsb' 0 128) in_vec
  let upper128 := Signal.map (BitVec.extractLsb' 128 128) in_vec
  let level7 := Signal.mux sel7 upper128 lower128
  
  -- Level 6: 128 -> 64 bits
  let sel6 := (sel >>> 6#8) &&& 1#8 === 1#8
  let lower64 := Signal.map (BitVec.extractLsb' 0 64) level7
  let upper64 := Signal.map (BitVec.extractLsb' 64 64) level7
  let level6 := Signal.mux sel6 upper64 lower64
  
  -- Level 5: 64 -> 32 bits
  let sel5 := (sel >>> 5#8) &&& 1#8 === 1#8
  let lower32 := Signal.map (BitVec.extractLsb' 0 32) level6
  let upper32 := Signal.map (BitVec.extractLsb' 32 32) level6
  let level5 := Signal.mux sel5 upper32 lower32
  
  -- Level 4: 32 -> 16 bits
  let sel4 := (sel >>> 4#8) &&& 1#8 === 1#8
  let lower16 := Signal.map (BitVec.extractLsb' 0 16) level5
  let upper16 := Signal.map (BitVec.extractLsb' 16 16) level5
  let level4 := Signal.mux sel4 upper16 lower16
  
  -- Level 3: 16 -> 8 bits
  let sel3 := (sel >>> 3#8) &&& 1#8 === 1#8
  let lower8 := Signal.map (BitVec.extractLsb' 0 8) level4
  let upper8 := Signal.map (BitVec.extractLsb' 8 8) level4
  let level3 := Signal.mux sel3 upper8 lower8
  
  -- Level 2: 8 -> 4 bits
  let sel2 := (sel >>> 2#8) &&& 1#8 === 1#8
  let lower4 := Signal.map (BitVec.extractLsb' 0 4) level3
  let upper4 := Signal.map (BitVec.extractLsb' 4 4) level3
  let level2 := Signal.mux sel2 upper4 lower4
  
  -- Level 1: 4 -> 2 bits
  let sel1 := (sel >>> 1#8) &&& 1#8 === 1#8
  let lower2 := Signal.map (BitVec.extractLsb' 0 2) level2
  let upper2 := Signal.map (BitVec.extractLsb' 2 2) level2
  let level1 := Signal.mux sel1 upper2 lower2
  
  -- Level 0: 2 -> 1 bit
  let sel0 := sel &&& 1#8 === 1#8
  let bit0 := Signal.map (BitVec.extractLsb' 0 1) level1
  let bit1 := Signal.map (BitVec.extractLsb' 1 1) level1
  Signal.mux sel0 bit1 bit0

#synthesizeVerilog prob018_mux256to1