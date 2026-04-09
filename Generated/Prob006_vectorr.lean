import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Bit reversal: reverses the bit ordering of an 8-bit input. -/
def prob006_vectorr {dom : DomainConfig}
    (input : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  Signal.map (fun x => 
    let b0 := x.extractLsb' 0 1  -- input bit 0
    let b1 := x.extractLsb' 1 1  -- input bit 1  
    let b2 := x.extractLsb' 2 1  -- input bit 2
    let b3 := x.extractLsb' 3 1  -- input bit 3
    let b4 := x.extractLsb' 4 1  -- input bit 4
    let b5 := x.extractLsb' 5 1  -- input bit 5
    let b6 := x.extractLsb' 6 1  -- input bit 6
    let b7 := x.extractLsb' 7 1  -- input bit 7
    b0 ++ b1 ++ b2 ++ b3 ++ b4 ++ b5 ++ b6 ++ b7  -- reverse order: bit 0 becomes MSB
  ) input

#synthesizeVerilog prob006_vectorr