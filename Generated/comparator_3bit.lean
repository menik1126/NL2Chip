import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 3-bit comparator: compares two 3-bit numbers and outputs A_greater, A_equal, A_less. -/
def comparator_3bit {dom : DomainConfig}
    (A B : Signal dom (BitVec 3))
    : Signal dom (BitVec 1 × (BitVec 1 × BitVec 1)) :=
  let A_greater := Signal.mux (Signal.ult B A) (Signal.pure 1#1) (Signal.pure 0#1)
  let A_equal := Signal.mux (A === B) (Signal.pure 1#1) (Signal.pure 0#1)
  let A_less := Signal.mux (Signal.ult A B) (Signal.pure 1#1) (Signal.pure 0#1)
  bundle2 A_greater (bundle2 A_equal A_less)

#synthesizeVerilog comparator_3bit
