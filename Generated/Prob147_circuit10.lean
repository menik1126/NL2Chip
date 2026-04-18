import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Sequential circuit with majority function feedback and XOR output -/
def prob147_circuit10 {dom : DomainConfig}
    (a b : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1) :=
  let c := Signal.loop fun state =>
    -- Majority function: a&b | a&state | b&state
    let next_c := (a &&& b) ||| (a &&& state) ||| (b &&& state)
    -- Register with initial value 0
    Signal.register 0#1 next_c
  -- Output q = a XOR b XOR c
  let q := a ^^^ b ^^^ c
  -- Bundle outputs: (q, state)
  bundle2 q c

#synthesizeVerilog prob147_circuit10
