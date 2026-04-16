import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Small 4-input AND reduction test -/
def test_and4 {dom : DomainConfig}
    (in_ : Signal dom (BitVec 4)) : Signal dom (BitVec 1) :=
  Signal.map (fun v =>
    let b0 := BitVec.extractLsb' 0 1 v
    let b1 := BitVec.extractLsb' 1 1 v
    let b2 := BitVec.extractLsb' 2 1 v
    let b3 := BitVec.extractLsb' 3 1 v
    b0 &&& b1 &&& b2 &&& b3) in_

#synthesizeVerilog test_and4
