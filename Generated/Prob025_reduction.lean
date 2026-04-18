import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Parity checker: computes even parity (XOR of all 8 bits) -/
def prob025_reduction {dom : DomainConfig}
    (input : Signal dom (BitVec 8)) : Signal dom (BitVec 1) :=
  Signal.map (fun (x : BitVec 8) =>
    -- Extract each bit as BitVec 1 and XOR them
    let b0 := BitVec.extractLsb 0 0 x
    let b1 := BitVec.extractLsb 1 1 x
    let b2 := BitVec.extractLsb 2 2 x
    let b3 := BitVec.extractLsb 3 3 x
    let b4 := BitVec.extractLsb 4 4 x
    let b5 := BitVec.extractLsb 5 5 x
    let b6 := BitVec.extractLsb 6 6 x
    let b7 := BitVec.extractLsb 7 7 x
    let xor01 := b0 ^^^ b1
    let xor23 := b2 ^^^ b3
    let xor45 := b4 ^^^ b5
    let xor67 := b6 ^^^ b7
    let xor0123 := xor01 ^^^ xor23
    let xor4567 := xor45 ^^^ xor67
    xor0123 ^^^ xor4567
  ) input

#synthesizeVerilog prob025_reduction
