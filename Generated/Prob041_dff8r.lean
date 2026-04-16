import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 8 D flip-flops with active-high synchronous reset setting output to zero. -/
def prob041_dff8r {dom : DomainConfig}
    (reset : Signal dom Bool) (d : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  let next := Signal.mux reset (Signal.pure 0#8) d
  Signal.register 0#8 next

#synthesizeVerilog prob041_dff8r
