import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Wire connection: output follows input directly. -/
def prob008_m2014_q4h {dom : DomainConfig}
    (input : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  input

#synthesizeVerilog prob008_m2014_q4h