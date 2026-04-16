import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test with explicit BitVec literals -/
def prob092_test3 {dom : DomainConfig}
    (inp : Signal dom (BitVec 100))
    : Signal dom (BitVec 100) :=
  
  let comp_both (x : BitVec 100) : BitVec 100 :=
    let shifted := x >>> 1
    x &&& shifted
  
  Signal.map comp_both inp

#synthesizeVerilog prob092_test3
