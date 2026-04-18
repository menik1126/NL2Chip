import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Wire declaration example: two AND gates feeding an OR gate, with inverted output. -/
def prob077_wire_decl {dom : DomainConfig}
    (a b c d : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1) :=
  let w1 := a &&& b           -- First AND gate
  let w2 := c &&& d           -- Second AND gate
  let out := w1 ||| w2        -- OR gate
  let out_n := ~~~out         -- NOT gate (inverted output)
  bundle2 out out_n

#synthesizeVerilog prob077_wire_decl
