import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Population count circuit: counts the number of '1's in a 3-bit input vector. -/
def prob009_popcount3 {dom : DomainConfig}
    (input : Signal dom (BitVec 3)) : Signal dom (BitVec 2) :=
  -- Extract individual bits using bitwise operations
  let bit0 := input &&& (1#3 : BitVec 3)  -- Extract bit 0
  let bit1 := (input &&& (2#3 : BitVec 3)) >>> 1#3  -- Extract bit 1, shift down
  let bit2 := (input &&& (4#3 : BitVec 3)) >>> 2#3  -- Extract bit 2, shift down
  -- Convert to 2-bit values for addition  
  let bit0_2 := Signal.map (fun x => BitVec.zeroExtend 2 x) bit0
  let bit1_2 := Signal.map (fun x => BitVec.zeroExtend 2 x) bit1  
  let bit2_2 := Signal.map (fun x => BitVec.zeroExtend 2 x) bit2
  -- Add them together
  bit0_2 + bit1_2 + bit2_2

#synthesizeVerilog prob009_popcount3