import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- D latch: when ena is high, q follows d; when ena is low, q holds its value -/
def prob028_m2014_q4a {dom : DomainConfig}
    (d : Signal dom (BitVec 1)) (ena : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  Signal.loop fun q =>
    let next := Signal.mux ena d q
    Signal.register 0#1 next

#synthesizeVerilog prob028_m2014_q4a
