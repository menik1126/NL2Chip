import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Vector splitter: splits 16-bit input into upper 8 bits and lower 8 bits. -/
def prob015_vector1 {dom : DomainConfig}
    (input : Signal dom (BitVec 16))
    : Signal dom (BitVec 8 × BitVec 8) :=
  -- Split 16-bit input: upper 8 bits [15:8] and lower 8 bits [7:0]
  let out_hi := Signal.map (fun x => BitVec.extractLsb' 8 8 x) input  -- upper 8 bits [15:8]
  let out_lo := Signal.map (fun x => BitVec.extractLsb' 0 8 x) input  -- lower 8 bits [7:0]
  bundle2 out_hi out_lo

#synthesizeVerilog prob015_vector1