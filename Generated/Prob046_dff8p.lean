import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 8 D flip-flops with active-high synchronous reset to 0x34, negedge-triggered. -/
def prob046_dff8p {dom : DomainConfig}
    (reset : Signal dom Bool) (d : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  Signal.registerNeg 0x34#8 (Signal.mux reset (Signal.pure 0x34#8) d)

#synthesizeVerilog prob046_dff8p
