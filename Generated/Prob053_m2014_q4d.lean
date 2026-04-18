import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- D flip-flop with XOR feedback: out <= in ^ out -/
def prob053_m2014_q4d {dom : DomainConfig}
    (inp : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  Signal.loop fun (out : Signal dom (BitVec 1)) =>
    let xor_result := inp ^^^ out
    Signal.register 0#1 xor_result

#synthesizeVerilog prob053_m2014_q4d
