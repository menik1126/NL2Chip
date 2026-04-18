import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Reverse byte order of a 32-bit vector. -/
def prob004_vector2 {dom : DomainConfig}
    (input : Signal dom (BitVec 32)) : Signal dom (BitVec 32) :=
  Signal.map (fun x =>
    let byte0 := BitVec.extractLsb 7 0 x   -- bits [7:0]
    let byte1 := BitVec.extractLsb 15 8 x  -- bits [15:8]
    let byte2 := BitVec.extractLsb 23 16 x -- bits [23:16]
    let byte3 := BitVec.extractLsb 31 24 x -- bits [31:24]
    -- Concatenate in reverse order: byte0 ++ byte1 ++ byte2 ++ byte3
    byte0 ++ byte1 ++ byte2 ++ byte3
  ) input

#synthesizeVerilog prob004_vector2
