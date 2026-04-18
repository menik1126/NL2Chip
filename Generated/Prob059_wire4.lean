import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Wire routing: connects a→w, b→x, b→y, c→z -/
def prob059_wire4 {dom : DomainConfig}
    (a b c : Signal dom (BitVec 1))
    : Signal dom ((BitVec 1 × BitVec 1) × (BitVec 1 × BitVec 1)) :=
  bundle2 (bundle2 a b) (bundle2 b c)

#synthesizeVerilog prob059_wire4
