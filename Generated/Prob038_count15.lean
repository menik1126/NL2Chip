import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 4-bit binary counter: counts 0 through 15 with active-high synchronous reset to 0. -/
def prob038_count15 {dom : DomainConfig}
    (reset : Signal dom Bool) : Signal dom (BitVec 4) :=
  Signal.loop fun (q : Signal dom (BitVec 4)) =>
    let nextVal := Signal.mux reset (Signal.pure 0#4) (q + 1#4)
    Signal.register 0#4 nextVal

#synthesizeVerilog prob038_count15
