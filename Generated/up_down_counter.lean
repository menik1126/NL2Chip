import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 16-bit up/down counter with synchronous reset -/
def up_down_counter {dom : DomainConfig}
    (reset : Signal dom Bool)
    (up_down : Signal dom Bool) : Signal dom (BitVec 16) :=
  Signal.loop fun (count : Signal dom (BitVec 16)) =>
    let nextVal := Signal.mux up_down (count + 1#16) (count - 1#16)
    let nextCount := Signal.mux reset (Signal.pure 0#16) nextVal
    Signal.register 0#16 nextCount

#synthesizeVerilog up_down_counter
