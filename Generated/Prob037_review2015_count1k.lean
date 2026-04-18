import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Counter that counts from 0 to 999 with synchronous reset. -/
def prob037_review2015_count1k {dom : DomainConfig}
    (reset : Signal dom Bool) : Signal dom (BitVec 10) :=
  Signal.loop fun (q : Signal dom (BitVec 10)) =>
    let atMax := q === 999#10
    let shouldReset := reset ||| atMax
    let nextVal := Signal.mux shouldReset (Signal.pure 0#10) (q + 1#10)
    Signal.register 0#10 nextVal

#synthesizeVerilog prob037_review2015_count1k
