import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- JK flip-flop with standard truth table -/
def prob056_ece241_2013_q7 {dom : DomainConfig}
    (j k : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  Signal.loop fun (q : Signal dom (BitVec 1)) =>
    let nextQ := (j &&& ~~~q) ||| (~~~k &&& q)
    Signal.register 0#1 nextQ

#synthesizeVerilog prob056_ece241_2013_q7
