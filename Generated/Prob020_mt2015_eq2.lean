import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 2-bit equality comparator: outputs 1 if A equals B, otherwise 0. -/
def prob020_mt2015_eq2 {dom : DomainConfig}
    (A B : Signal dom (BitVec 2)) : Signal dom (BitVec 1) :=
  let equal := A === B
  Signal.mux equal (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob020_mt2015_eq2