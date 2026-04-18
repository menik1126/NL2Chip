import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Split 16-bit input into upper and lower 8-bit bytes. -/
def prob015_vector1 {dom : DomainConfig}
    (input : Signal dom (BitVec 16))
    : Signal dom (BitVec 8 × BitVec 8) :=
  let out_lo := Signal.map (fun x => BitVec.extractLsb' 0 8 x) input
  let out_hi := Signal.map (fun x => BitVec.extractLsb' 8 8 x) input
  bundle2 out_hi out_lo

#synthesizeVerilog prob015_vector1
