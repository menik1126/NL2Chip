import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Combinational circuit: q = ~a ^ b ^ c ^ d -/
def prob103_circuit2 {dom : DomainConfig}
    (a b c d : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  (~~~a) ^^^ b ^^^ c ^^^ d

#synthesizeVerilog prob103_circuit2
