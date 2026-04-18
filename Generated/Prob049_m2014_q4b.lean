import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- D flip-flop with asynchronous reset: when ar is high, output is 0; otherwise, output follows d. -/
def prob049_m2014_q4b {dom : DomainConfig}
    (ar : Signal dom Bool)
    (d : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  let nextVal := Signal.mux ar (Signal.pure 0#1) d
  Signal.register 0#1 nextVal

#synthesizeVerilog prob049_m2014_q4b
