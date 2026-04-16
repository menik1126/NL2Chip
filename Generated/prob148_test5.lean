import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test: extractLsb -/
def prob148_test5 {dom : DomainConfig}
    (r : Signal dom (BitVec 3))
    : Signal dom (BitVec 1) :=
  let r0 : Signal dom (BitVec 1) := Signal.map (fun x => BitVec.extractLsb' 0 1 x) r
  r0

#synthesizeVerilog prob148_test5
