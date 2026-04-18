import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- XOR gate implemented three ways: assign, combinational, and clocked -/
def prob058_alwaysblock2 {dom : DomainConfig}
    (a b : Signal dom (BitVec 1))
    : Signal dom ((BitVec 1 × BitVec 1) × BitVec 1) :=
  let out_assign := a ^^^ b
  let out_always_comb := a ^^^ b
  let out_always_ff := Signal.register 0#1 (a ^^^ b)
  bundle2 (bundle2 out_assign out_always_comb) out_always_ff

#synthesizeVerilog prob058_alwaysblock2
