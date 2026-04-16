import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 2-bit equality comparator: z = 1 if A == B, else z = 0. -/
def prob020_mt2015_eq2 {dom : DomainConfig}
    (A B : Signal dom (BitVec 2)) : Signal dom (BitVec 1) :=
  let eq : Signal dom Bool := A === B
  Signal.mux eq (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob020_mt2015_eq2
