import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test: extractLsb + equality to Bool -/
def prob148_test6 {dom : DomainConfig}
    (r : Signal dom (BitVec 3))
    : Signal dom Bool :=
  let r0_bv : Signal dom (BitVec 1) := Signal.map (fun x => BitVec.extractLsb' 0 1 x) r
  let r0 : Signal dom Bool := r0_bv === Signal.pure 1#1
  r0

#synthesizeVerilog prob148_test6
