import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- D flip-flop with active high synchronous reset: on each clock edge, if r=1 then q<=0 else q<=d. -/
def prob048_m2014_q4c {dom : DomainConfig}
    (d : Signal dom (BitVec 1)) (r : Signal dom Bool) : Signal dom (BitVec 1) :=
  Signal.register 0#1 (Signal.mux r (Signal.pure 0#1) d)

#synthesizeVerilog prob048_m2014_q4c
