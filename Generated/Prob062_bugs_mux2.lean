import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 8-bit 2-to-1 multiplexer (bug-fixed version): sel=true selects a, sel=false selects b. -/
def prob062_bugs_mux2 {dom : DomainConfig}
    (sel : Signal dom Bool)
    (a b : Signal dom (BitVec 8))
    : Signal dom (BitVec 8) :=
  Signal.mux sel a b

#synthesizeVerilog prob062_bugs_mux2
