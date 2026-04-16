import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Reverses the byte order of a 32-bit vector: {in[7:0], in[15:8], in[23:16], in[31:24]} -/
def prob004_vector2 {dom : DomainConfig}
    (in_ : Signal dom (BitVec 32)) : Signal dom (BitVec 32) :=
  Signal.map (fun v =>
    let byte0 := BitVec.extractLsb' 0  8 v  -- in[7:0]
    let byte1 := BitVec.extractLsb' 8  8 v  -- in[15:8]
    let byte2 := BitVec.extractLsb' 16 8 v  -- in[23:16]
    let byte3 := BitVec.extractLsb' 24 8 v  -- in[31:24]
    -- Result: {in[7:0], in[15:8], in[23:16], in[31:24]}
    -- In BitVec ++ notation, left operand is the high bits
    byte0 ++ byte1 ++ byte2 ++ byte3
  ) in_

#synthesizeVerilog prob004_vector2
