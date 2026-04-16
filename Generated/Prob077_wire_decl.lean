import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Two-layer circuit: AND-OR with inverted output. out = (a&b)|(c&d), out_n = ~out -/
def prob077_wire_decl {dom : DomainConfig}
    (a b c d : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1) :=
  let w1  := a &&& b          -- AND gate: a and b
  let w2  := c &&& d          -- AND gate: c and d
  let out := w1 ||| w2        -- OR gate: combine both AND outputs
  let out_n := ~~~out         -- NOT gate: invert out
  bundle2 out out_n

#synthesizeVerilog prob077_wire_decl
