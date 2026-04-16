import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 2-to-1 multiplexer: sel=true selects b, sel=false selects a. -/
def prob022_mux2to1 {dom : DomainConfig}
    (a b : Signal dom (BitVec 1)) (sel : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  Signal.mux sel b a

#synthesizeVerilog prob022_mux2to1
