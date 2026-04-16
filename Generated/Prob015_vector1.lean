import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Splits a 16-bit input into upper [15:8] and lower [7:0] bytes. -/
def prob015_vector1 {dom : DomainConfig}
    (in_ : Signal dom (BitVec 16))
    : Signal dom (BitVec 8 × BitVec 8) :=
  let out_hi := Signal.map (fun v => BitVec.extractLsb' 8 8 v) in_
  let out_lo := Signal.map (fun v => BitVec.extractLsb' 0 8 v) in_
  bundle2 out_hi out_lo

#synthesizeVerilog prob015_vector1
