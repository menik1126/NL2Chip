import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 8-bit D flip-flop: delays 8-bit input by one clock cycle. -/
def prob034_dff8 {dom : DomainConfig}
    (d : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  Signal.register 0#8 d

#synthesizeVerilog prob034_dff8
