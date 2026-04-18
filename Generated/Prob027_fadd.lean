import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Full adder: adds three 1-bit inputs (a, b, cin), returns bundled (sum, cout). -/
def prob027_fadd {dom : DomainConfig}
    (a b cin : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1) :=
  let sum  := a ^^^ b ^^^ cin
  let cout := (a &&& b) ||| (a &&& cin) ||| (b &&& cin)
  bundle2 sum cout

#synthesizeVerilog prob027_fadd
