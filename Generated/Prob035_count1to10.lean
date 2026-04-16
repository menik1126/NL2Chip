import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Decade counter: counts 1 through 10 with synchronous reset to 1. -/
def prob035_count1to10 {dom : DomainConfig}
    (reset : Signal dom Bool) : Signal dom (BitVec 4) :=
  Signal.loop fun (q : Signal dom (BitVec 4)) =>
    let atMax := q === 10#4
    let shouldReset := reset ||| atMax
    let nextVal := Signal.mux shouldReset (Signal.pure 1#4) (q + 1#4)
    Signal.register 1#4 nextVal

#synthesizeVerilog prob035_count1to10
