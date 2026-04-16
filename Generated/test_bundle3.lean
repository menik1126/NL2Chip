import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 4-input AND/OR/XOR reduction test with bundle3 -/
def test_bundle3_small {dom : DomainConfig}
    (in_ : Signal dom (BitVec 4))
    : Signal dom (BitVec 1 × BitVec 1 × BitVec 1) :=
  let out_and := Signal.map (fun v =>
    let b0 := BitVec.extractLsb' 0 1 v
    let b1 := BitVec.extractLsb' 1 1 v
    let b2 := BitVec.extractLsb' 2 1 v
    let b3 := BitVec.extractLsb' 3 1 v
    b0 &&& b1 &&& b2 &&& b3) in_
  let out_or := Signal.map (fun v =>
    let b0 := BitVec.extractLsb' 0 1 v
    let b1 := BitVec.extractLsb' 1 1 v
    let b2 := BitVec.extractLsb' 2 1 v
    let b3 := BitVec.extractLsb' 3 1 v
    b0 ||| b1 ||| b2 ||| b3) in_
  let out_xor := Signal.map (fun v =>
    let b0 := BitVec.extractLsb' 0 1 v
    let b1 := BitVec.extractLsb' 1 1 v
    let b2 := BitVec.extractLsb' 2 1 v
    let b3 := BitVec.extractLsb' 3 1 v
    b0 ^^^ b1 ^^^ b2 ^^^ b3) in_
  bundle3 out_and out_or out_xor

#synthesizeVerilog test_bundle3_small
