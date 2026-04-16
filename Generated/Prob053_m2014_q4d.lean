import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- D flip-flop with XOR feedback: out <= in ^ out on each positive clock edge. -/
def prob053_m2014_q4d {dom : DomainConfig}
    (in_ : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  Signal.loop fun (out : Signal dom (BitVec 1)) =>
    let next := in_ ^^^ out
    Signal.register 0#1 next

#synthesizeVerilog prob053_m2014_q4d
