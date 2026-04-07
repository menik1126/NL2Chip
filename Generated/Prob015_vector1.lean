import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Splits 16-bit input into upper 8 bits (out_hi) and lower 8 bits (out_lo). -/
def prob015_vector1 {dom : DomainConfig}
    (input : Signal dom (BitVec 16))
    : Signal dom (BitVec 8 × BitVec 8) :=
  let out_lo := Signal.map (BitVec.extractLsb' 0 8) input   -- Lower 8 bits [7:0]
  let out_hi := Signal.map (BitVec.extractLsb' 8 8) input   -- Upper 8 bits [15:8]
  bundle2 out_hi out_lo

#synthesizeVerilog prob015_vector1