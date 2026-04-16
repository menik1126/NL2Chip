import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Population count: counts the number of '1' bits in a 3-bit input, returning a 2-bit result. -/
def prob009_popcount3 {dom : DomainConfig}
    (in_ : Signal dom (BitVec 3)) : Signal dom (BitVec 2) :=
  Signal.map (fun v =>
    let b0 : BitVec 2 := (v.extractLsb 0 0).zeroExtend 2
    let b1 : BitVec 2 := (v.extractLsb 1 1).zeroExtend 2
    let b2 : BitVec 2 := (v.extractLsb 2 2).zeroExtend 2
    b0 + b1 + b2) in_

#synthesizeVerilog prob009_popcount3
