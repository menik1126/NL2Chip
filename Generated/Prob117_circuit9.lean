import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Sequential circuit: loads 4 when a=1, else counts up wrapping at 6 to 0 -/
def prob117_circuit9 {dom : DomainConfig}
    (a : Signal dom Bool) : Signal dom (BitVec 3) :=
  Signal.loop fun (q : Signal dom (BitVec 3)) =>
    let atMax := q === 6#3
    let nextCount := Signal.mux atMax (Signal.pure 0#3) (q + 1#3)
    let nextVal := Signal.mux a (Signal.pure 4#3) nextCount
    Signal.register 4#3 nextVal

#synthesizeVerilog prob117_circuit9
