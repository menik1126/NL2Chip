import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_xor100 {dom : DomainConfig}
    (input : Signal dom (BitVec 100)) : Signal dom (BitVec 1) :=
  let bit0 := Signal.map (fun x => x.extractLsb 0 0) input
  let bit1 := Signal.map (fun x => x.extractLsb 1 1) input
  let bit2 := Signal.map (fun x => x.extractLsb 2 2) input
  let bit3 := Signal.map (fun x => x.extractLsb 3 3) input
  -- XOR using ^^^
  bit0 ^^^ bit1 ^^^ bit2 ^^^ bit3

#synthesizeVerilog test_xor100
