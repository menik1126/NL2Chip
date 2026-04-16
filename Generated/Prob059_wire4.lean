import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Wire connections: a->w, b->x, b->y, c->z (b fans out to both x and y). -/
def prob059_wire4 {dom : DomainConfig}
    (a b c : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1 × BitVec 1 × BitVec 1) :=
  let w := a
  let x := b
  let y := b
  let z := c
  bundle2 w (bundle2 x (bundle2 y z))

#synthesizeVerilog prob059_wire4
