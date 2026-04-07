import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Reverses the byte order of a 32-bit vector. -/
def prob004_vector2 {dom : DomainConfig}
    (input : Signal dom (BitVec 32)) : Signal dom (BitVec 32) :=
  -- Extract each byte using Signal.map with BitVec.extractLsb'
  let byte0 := input.map (BitVec.extractLsb' 0 8 ·)   -- bits [7:0]  
  let byte1 := input.map (BitVec.extractLsb' 8 8 ·)   -- bits [15:8]
  let byte2 := input.map (BitVec.extractLsb' 16 8 ·)  -- bits [23:16]
  let byte3 := input.map (BitVec.extractLsb' 24 8 ·)  -- bits [31:24]
  
  -- Reverse the byte order using concatenation
  -- Result: {byte0, byte1, byte2, byte3} where byte0 goes to [31:24], etc.
  let result_hi := byte0 ++ byte1   -- [31:16] = byte0 ++ byte1
  let result_lo := byte2 ++ byte3   -- [15:0]  = byte2 ++ byte3
  result_hi ++ result_lo            -- [31:0]  = result_hi ++ result_lo

#synthesizeVerilog prob004_vector2