import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 100-input AND reduction -/
def test_and100 {dom : DomainConfig}
    (in_ : Signal dom (BitVec 100)) : Signal dom (BitVec 1) :=
  Signal.map (fun v =>
    let b0 := BitVec.extractLsb' 0 1 v
    let b1 := BitVec.extractLsb' 1 1 v
    let b2 := BitVec.extractLsb' 2 1 v
    let b3 := BitVec.extractLsb' 3 1 v
    let b4 := BitVec.extractLsb' 4 1 v
    let b5 := BitVec.extractLsb' 5 1 v
    let b6 := BitVec.extractLsb' 6 1 v
    let b7 := BitVec.extractLsb' 7 1 v
    let b8 := BitVec.extractLsb' 8 1 v
    let b9 := BitVec.extractLsb' 9 1 v
    b0 &&& b1 &&& b2 &&& b3 &&& b4 &&& b5 &&& b6 &&& b7 &&& b8 &&& b9) in_

#synthesizeVerilog test_and100
