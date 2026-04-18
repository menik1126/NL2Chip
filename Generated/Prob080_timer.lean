import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Timer that counts down from a loaded value and asserts tc when reaching 0. -/
def prob080_timer {dom : DomainConfig}
    (load : Signal dom Bool)
    (data : Signal dom (BitVec 10)) : Signal dom Bool :=
  let count := Signal.loop fun (count : Signal dom (BitVec 10)) =>
    let isZero := count === 0#10
    let nextCount := Signal.mux load data
      (Signal.mux isZero count (count - 1#10))
    Signal.register 0#10 nextCount
  count === 0#10

#synthesizeVerilog prob080_timer
