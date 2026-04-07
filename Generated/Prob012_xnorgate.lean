import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- XNOR gate: returns true when inputs are equal (both 0 or both 1). -/
def prob012_xnorgate {dom : DomainConfig}
    (a b : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  ~~~(a ^^^ b)

#synthesizeVerilog prob012_xnorgate