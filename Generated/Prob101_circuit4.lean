import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Combinational circuit: q = b | c (inputs a and d are unused). -/
def prob101_circuit4 {dom : DomainConfig}
    (a b c d : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  b ||| c

#synthesizeVerilog prob101_circuit4
