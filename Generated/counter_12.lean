import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Counter from 0 to 11 with enable control -/
def counter_12 {dom : DomainConfig}
    (valid_count : Signal dom Bool) : Signal dom (BitVec 4) :=
  Signal.loop fun (q : Signal dom (BitVec 4)) =>
    let atMax := q === 11#4
    let nextCount := Signal.mux atMax (Signal.pure 0#4) (q + 1#4)
    let nextVal := Signal.mux valid_count nextCount q
    Signal.register 0#4 nextVal

#synthesizeVerilog counter_12
