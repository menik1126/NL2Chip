import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Timer: counts down from a loaded 10-bit value, asserting tc when count reaches 0.
    If load=1, load counter with data. If load=0, decrement unless count is already 0. -/
def prob080_timer {dom : DomainConfig}
    (load : Signal dom Bool) (data : Signal dom (BitVec 10))
    : Signal dom Bool :=
  -- Use Signal.loop to maintain the count register with feedback
  let count : Signal dom (BitVec 10) :=
    Signal.loop fun (count : Signal dom (BitVec 10)) =>
      let isZero := count === (Signal.pure 0#10)
      -- If load: take data; else if not zero: decrement; else stay at 0
      let decremented := count - 1#10
      let countIfNotLoad := Signal.mux isZero (Signal.pure 0#10) decremented
      let nextCount := Signal.mux load data countIfNotLoad
      Signal.register 0#10 nextCount
  -- tc asserted when count == 0
  count === (Signal.pure 0#10)

#synthesizeVerilog prob080_timer
