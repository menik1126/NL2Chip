import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Population count: counts the number of 1's in a 3-bit input. -/
def prob009_popcount3 {dom : DomainConfig}
    (input : Signal dom (BitVec 3)) : Signal dom (BitVec 2) :=
  -- Extract individual bits using extractLsb
  let bit0 := Signal.map (fun x => BitVec.zeroExtend 2 (x.extractLsb 0 0)) input
  let bit1 := Signal.map (fun x => BitVec.zeroExtend 2 (x.extractLsb 1 1)) input
  let bit2 := Signal.map (fun x => BitVec.zeroExtend 2 (x.extractLsb 2 2)) input
  bit0 + bit1 + bit2

#synthesizeVerilog prob009_popcount3
