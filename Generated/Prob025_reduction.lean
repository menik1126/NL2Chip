import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Even parity: XOR of all 8 input bits, producing a 1-bit parity output. -/
def prob025_reduction {dom : DomainConfig}
    (in_ : Signal dom (BitVec 8)) : Signal dom (BitVec 1) :=
  Signal.map (fun v =>
    let b0 : BitVec 1 := v.extractLsb' 0 1
    let b1 : BitVec 1 := v.extractLsb' 1 1
    let b2 : BitVec 1 := v.extractLsb' 2 1
    let b3 : BitVec 1 := v.extractLsb' 3 1
    let b4 : BitVec 1 := v.extractLsb' 4 1
    let b5 : BitVec 1 := v.extractLsb' 5 1
    let b6 : BitVec 1 := v.extractLsb' 6 1
    let b7 : BitVec 1 := v.extractLsb' 7 1
    b0 ^^^ b1 ^^^ b2 ^^^ b3 ^^^ b4 ^^^ b5 ^^^ b6 ^^^ b7
  ) in_

#synthesizeVerilog prob025_reduction
