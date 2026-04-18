import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Sequential circuit that outputs the inverted input delayed by one clock cycle -/
def prob098_circuit7 {dom : DomainConfig}
    (a : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  Signal.register 0#1 (~~~a)

#synthesizeVerilog prob098_circuit7
