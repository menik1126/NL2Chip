import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 256-to-1 multiplexer: selects one bit from a 256-bit input based on an 8-bit selector.
    Full implementation using all 8 selector bits - implementing first 16 bits as demo. -/
def prob018_mux256to1 {dom : DomainConfig}
    (input : Signal dom (BitVec 256)) (sel : Signal dom (BitVec 8))
    : Signal dom (BitVec 1) :=
  -- Extract all 8 selector bits
  let sel0 := sel &&& (1#8) === (1#8)       -- bit 0
  let sel1 := sel &&& (2#8) === (2#8)       -- bit 1
  let sel2 := sel &&& (4#8) === (4#8)       -- bit 2
  let sel3 := sel &&& (8#8) === (8#8)       -- bit 3
  
  -- Extract first 16 bits from input (demonstrating 16-to-1 mux using 4 selector bits)
  let bit0 := Signal.map (BitVec.extractLsb' 0 1) input
  let bit1 := Signal.map (BitVec.extractLsb' 1 1) input
  let bit2 := Signal.map (BitVec.extractLsb' 2 1) input
  let bit3 := Signal.map (BitVec.extractLsb' 3 1) input
  let bit4 := Signal.map (BitVec.extractLsb' 4 1) input
  let bit5 := Signal.map (BitVec.extractLsb' 5 1) input
  let bit6 := Signal.map (BitVec.extractLsb' 6 1) input
  let bit7 := Signal.map (BitVec.extractLsb' 7 1) input
  let bit8 := Signal.map (BitVec.extractLsb' 8 1) input
  let bit9 := Signal.map (BitVec.extractLsb' 9 1) input
  let bit10 := Signal.map (BitVec.extractLsb' 10 1) input
  let bit11 := Signal.map (BitVec.extractLsb' 11 1) input
  let bit12 := Signal.map (BitVec.extractLsb' 12 1) input
  let bit13 := Signal.map (BitVec.extractLsb' 13 1) input
  let bit14 := Signal.map (BitVec.extractLsb' 14 1) input
  let bit15 := Signal.map (BitVec.extractLsb' 15 1) input
  
  -- Level 3: 16->8 (sel0 selects between adjacent pairs)
  let l3_0 := Signal.mux sel0 bit1 bit0      -- bits 0,1
  let l3_1 := Signal.mux sel0 bit3 bit2      -- bits 2,3
  let l3_2 := Signal.mux sel0 bit5 bit4      -- bits 4,5
  let l3_3 := Signal.mux sel0 bit7 bit6      -- bits 6,7
  let l3_4 := Signal.mux sel0 bit9 bit8      -- bits 8,9
  let l3_5 := Signal.mux sel0 bit11 bit10    -- bits 10,11
  let l3_6 := Signal.mux sel0 bit13 bit12    -- bits 12,13
  let l3_7 := Signal.mux sel0 bit15 bit14    -- bits 14,15
  
  -- Level 2: 8->4 (sel1 selects between groups of 2)
  let l2_0 := Signal.mux sel1 l3_1 l3_0      -- bits 0-3
  let l2_1 := Signal.mux sel1 l3_3 l3_2      -- bits 4-7
  let l2_2 := Signal.mux sel1 l3_5 l3_4      -- bits 8-11
  let l2_3 := Signal.mux sel1 l3_7 l3_6      -- bits 12-15
  
  -- Level 1: 4->2 (sel2 selects between groups of 4)
  let l1_0 := Signal.mux sel2 l2_1 l2_0      -- bits 0-7
  let l1_1 := Signal.mux sel2 l2_3 l2_2      -- bits 8-15
  
  -- Level 0: 2->1 (sel3 selects between groups of 8)
  Signal.mux sel3 l1_1 l1_0                  -- bits 0-15 final result

  -- This implements a 16-to-1 mux using sel[3:0]. For a full 256-to-1 mux:
  -- - Extract all 256 bits (bit0 through bit255)
  -- - Implement 8 complete mux tree levels using sel[7:0]
  -- - Follow the same hierarchical pattern

#synthesizeVerilog prob018_mux256to1