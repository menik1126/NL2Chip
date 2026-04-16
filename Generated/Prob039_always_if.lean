import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 2-to-1 mux: choose b if both sel_b1 and sel_b2 are true, otherwise choose a.
    Implemented twice as out_assign and out_always. -/
def prob039_always_if {dom : DomainConfig}
    (a b sel_b1 sel_b2 : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1) :=
  let sel := sel_b1 &&& sel_b2
  let out := Signal.mux (sel === Signal.pure 1#1) b a
  bundle2 out out

#synthesizeVerilog prob039_always_if
