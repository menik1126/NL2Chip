import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Shift register stage with load and enable control -/
def prob061_2014_q4a {dom : DomainConfig}
    (w : Signal dom (BitVec 1))
    (R : Signal dom (BitVec 1))
    (E : Signal dom Bool)
    (L : Signal dom Bool) : Signal dom (BitVec 1) :=
  Signal.loop fun (Q : Signal dom (BitVec 1)) =>
    let nextVal := Signal.mux L R (Signal.mux E w Q)
    Signal.register 0#1 nextVal

#synthesizeVerilog prob061_2014_q4a
