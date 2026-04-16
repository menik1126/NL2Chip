import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Sequential circuit: when a=1 load q=4; when a=0 and q=6 wrap to 0; otherwise increment. -/
def prob117_circuit9 {dom : DomainConfig}
    (a : Signal dom Bool) : Signal dom (BitVec 3) :=
  Signal.loop fun (q : Signal dom (BitVec 3)) =>
    let atSix := q === (Signal.pure 6#3)
    let incremented := q + 1#3
    let wrappedOrInc := Signal.mux atSix (Signal.pure 0#3) incremented
    let nextVal := Signal.mux a (Signal.pure 4#3) wrappedOrInc
    Signal.register 4#3 nextVal

#synthesizeVerilog prob117_circuit9
