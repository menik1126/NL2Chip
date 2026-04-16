import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 8 D flip-flops: all triggered on positive clock edge, output q follows input d after one cycle. -/
def prob034_dff8 {dom : DomainConfig}
    (d : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  Signal.register 0#8 d

#synthesizeVerilog prob034_dff8
