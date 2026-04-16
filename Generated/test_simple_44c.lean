import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_simple_44c {dom : DomainConfig}
    (a b : Signal dom (BitVec 3))
    : Signal dom (BitVec 3 × BitVec 1) :=
  let or_result : Signal dom (BitVec 3) := a ||| b
  -- Use extractLsb' to get each bit as BitVec 1
  let bit0 : Signal dom (BitVec 1) := Signal.map (fun v => BitVec.extractLsb' 0 1 v) or_result
  let bit1 : Signal dom (BitVec 1) := Signal.map (fun v => BitVec.extractLsb' 1 1 v) or_result
  let bit2 : Signal dom (BitVec 1) := Signal.map (fun v => BitVec.extractLsb' 2 1 v) or_result
  let out_or_logical : Signal dom (BitVec 1) := bit0 ||| bit1 ||| bit2
  bundle2 a out_or_logical

#synthesizeVerilog test_simple_44c
