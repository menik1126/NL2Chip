import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 8-bit 2-to-1 mux: sel=true selects a, sel=false selects b (fixes output width bug). -/
def prob062_bugs_mux2 {dom : DomainConfig}
    (sel : Signal dom Bool) (a b : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  Signal.mux sel a b

#synthesizeVerilog prob062_bugs_mux2
