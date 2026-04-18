import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Decade counter (0-9) with enable signal and synchronous reset. -/
def prob067_countslow {dom : DomainConfig}
    (slowena : Signal dom Bool)
    (reset : Signal dom Bool) : Signal dom (BitVec 4) :=
  Signal.loop fun (q : Signal dom (BitVec 4)) =>
    let atMax := q === 9#4
    let nextCount := Signal.mux atMax (Signal.pure 0#4) (q + 1#4)
    let nextVal := Signal.mux reset 
      (Signal.pure 0#4)
      (Signal.mux slowena nextCount q)
    Signal.register 0#4 nextVal

#synthesizeVerilog prob067_countslow
