import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Bit reversal: reverses the bit ordering of an 8-bit input vector. -/
def prob006_vectorr {dom : DomainConfig}
    (input : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  Signal.map (fun x => 
    let b0 := BitVec.extractLsb' 0 1 x
    let b1 := BitVec.extractLsb' 1 1 x
    let b2 := BitVec.extractLsb' 2 1 x
    let b3 := BitVec.extractLsb' 3 1 x
    let b4 := BitVec.extractLsb' 4 1 x
    let b5 := BitVec.extractLsb' 5 1 x
    let b6 := BitVec.extractLsb' 6 1 x
    let b7 := BitVec.extractLsb' 7 1 x
    -- Reverse order: b0 becomes MSB, b7 becomes LSB
    b0 ++ b1 ++ b2 ++ b3 ++ b4 ++ b5 ++ b6 ++ b7
  ) input

#synthesizeVerilog prob006_vectorr
