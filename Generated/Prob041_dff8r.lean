import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 8-bit D flip-flop with active high synchronous reset. -/
def prob041_dff8r {dom : DomainConfig}
    (reset : Signal dom Bool)
    (d : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  let nextVal := Signal.mux reset (Signal.pure 0#8) d
  Signal.register 0#8 nextVal

#synthesizeVerilog prob041_dff8r
