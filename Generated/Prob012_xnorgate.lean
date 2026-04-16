import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- XNOR gate: outputs the complement of XOR of two 1-bit inputs. -/
def prob012_xnorgate {dom : DomainConfig}
    (a b : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  ~~~(a ^^^ b)

#synthesizeVerilog prob012_xnorgate
