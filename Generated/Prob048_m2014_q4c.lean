import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- D flip-flop with active high synchronous reset. -/
def prob048_m2014_q4c {dom : DomainConfig}
    (d : Signal dom (BitVec 1))
    (r : Signal dom Bool) : Signal dom (BitVec 1) :=
  Signal.loop fun (q : Signal dom (BitVec 1)) =>
    let nextVal := Signal.mux r (Signal.pure 0#1) d
    Signal.register 0#1 nextVal

#synthesizeVerilog prob048_m2014_q4c
