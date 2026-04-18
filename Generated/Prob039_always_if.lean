import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 2-to-1 mux with two select signals: chooses b if both sel_b1 and sel_b2 are true, otherwise a. -/
def prob039_always_if {dom : DomainConfig}
    (a b : Signal dom (BitVec 1))
    (sel_b1 sel_b2 : Signal dom Bool)
    : Signal dom (BitVec 1 × BitVec 1) :=
  let sel := sel_b1 &&& sel_b2
  let out_assign := Signal.mux sel b a
  let out_always := Signal.mux sel b a
  bundle2 out_assign out_always

#synthesizeVerilog prob039_always_if
