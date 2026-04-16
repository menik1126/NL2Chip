import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Decade counter: counts 0 through 9 with synchronous active-high reset to 0. -/
def prob040_count10 {dom : DomainConfig}
    (reset : Signal dom Bool) : Signal dom (BitVec 4) :=
  Signal.loop fun (q : Signal dom (BitVec 4)) =>
    let atMax := q === 9#4
    let shouldReset := reset ||| atMax
    let nextVal := Signal.mux shouldReset (Signal.pure 0#4) (q + 1#4)
    Signal.register 0#4 nextVal

#synthesizeVerilog prob040_count10
