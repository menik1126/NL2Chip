import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 8-bit D flip-flop with synchronous reset to 0x34, negative edge triggered -/
def prob046_dff8p {dom : DomainConfig}
    (reset : Signal dom Bool)
    (d : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  let nextVal := Signal.mux reset (Signal.pure 52#8) d
  Signal.register 52#8 nextVal

#synthesizeVerilog prob046_dff8p
