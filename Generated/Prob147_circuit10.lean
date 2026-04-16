import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Sequential circuit with majority-function flip-flop and XOR output.
    state (c) <= a&b | a&c | b&c (majority of a, b, c)
    q = a ^ b ^ state -/
def prob147_circuit10 {dom : DomainConfig}
    (a b : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1) :=
  -- Use Signal.loop for feedback on state c
  let state : Signal dom (BitVec 1) :=
    Signal.loop fun (c : Signal dom (BitVec 1)) =>
      -- next c = a&b | a&c | b&c (majority function)
      let ab := a &&& b
      let ac := a &&& c
      let bc := b &&& c
      let nextC := ab ||| ac ||| bc
      Signal.register 0#1 nextC
  -- q = a ^ b ^ state
  let q := a ^^^ b ^^^ state
  bundle2 q state

#synthesizeVerilog prob147_circuit10
