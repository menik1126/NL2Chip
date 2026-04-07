import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 2-input AND gate: outputs a & b. -/
def prob014_andgate {dom : DomainConfig}
    (a b : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  a &&& b

#synthesizeVerilog prob014_andgate