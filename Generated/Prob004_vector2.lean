import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Reverse byte order of a 32-bit vector -/
def prob004_vector2 {dom : DomainConfig}
    (input : Signal dom (BitVec 32)) : Signal dom (BitVec 32) :=
  -- Extract bytes using mask and shift operations
  let byte0 := input &&& 255#32                   -- bits [7:0], mask with 0xFF
  let byte1 := (input >>> 8#32) &&& 255#32       -- bits [15:8]  
  let byte2 := (input >>> 16#32) &&& 255#32      -- bits [23:16]
  let byte3 := (input >>> 24#32) &&& 255#32      -- bits [31:24]
  -- Concatenate in reverse order: byte0 becomes MSB, byte3 becomes LSB
  (byte0 <<< 24#32) ||| (byte1 <<< 16#32) ||| (byte2 <<< 8#32) ||| byte3

#synthesizeVerilog prob004_vector2