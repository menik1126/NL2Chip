import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Half adder: adds two 1-bit inputs, returns bundled (sum, cout). -/
def prob024_hadd {dom : DomainConfig}
    (a b : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1) :=
  let sum  := a ^^^ b         -- XOR for sum
  let cout := a &&& b         -- AND for carry-out
  bundle2 sum cout

#synthesizeVerilog prob024_hadd
