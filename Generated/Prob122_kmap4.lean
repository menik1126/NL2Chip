import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Karnaugh map with 4 inputs: output is a XOR b XOR c XOR d. -/
def prob122_kmap4 {dom : DomainConfig}
    (a b c d : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  a ^^^ b ^^^ c ^^^ d

#synthesizeVerilog prob122_kmap4
