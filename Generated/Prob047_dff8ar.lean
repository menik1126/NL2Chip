import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 8-bit D flip-flop with active high asynchronous reset to 0. -/
def prob047_dff8ar {dom : DomainConfig}
    (areset : Signal dom Bool)
    (d : Signal dom (BitVec 8))
    : Signal dom (BitVec 8) :=
  let nextVal := Signal.mux areset (Signal.pure 0#8) d
  Signal.register 0#8 nextVal

#synthesizeVerilog prob047_dff8ar
