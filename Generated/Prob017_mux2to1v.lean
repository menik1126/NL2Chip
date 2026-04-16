import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 100-bit wide 2-to-1 multiplexer: sel=true selects b, sel=false selects a. -/
def prob017_mux2to1v {dom : DomainConfig}
    (a b : Signal dom (BitVec 100)) (sel : Signal dom Bool)
    : Signal dom (BitVec 100) :=
  Signal.mux sel b a

#synthesizeVerilog prob017_mux2to1v
