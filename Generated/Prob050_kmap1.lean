import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Karnaugh map circuit: out = a | b | c (out is 0 only when a=b=c=0). -/
def prob050_kmap1 {dom : DomainConfig}
    (a b c : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  a ||| b ||| c

#synthesizeVerilog prob050_kmap1
