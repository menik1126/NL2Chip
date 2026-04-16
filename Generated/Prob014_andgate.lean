import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- AND gate: computes the bitwise AND of two 1-bit input signals. -/
def prob014_andgate {dom : DomainConfig}
    (a b : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  a &&& b

#synthesizeVerilog prob014_andgate
