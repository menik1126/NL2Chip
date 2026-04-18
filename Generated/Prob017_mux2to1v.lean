import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 2-to-1 multiplexer (100-bit): sel=true selects b, sel=false selects a. -/
def prob017_mux2to1v {dom : DomainConfig}
    (a b : Signal dom (BitVec 100)) (sel : Signal dom Bool)
    : Signal dom (BitVec 100) :=
  Signal.mux sel b a

#synthesizeVerilog prob017_mux2to1v
