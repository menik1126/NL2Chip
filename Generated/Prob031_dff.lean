import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- D flip-flop: captures input d on the positive clock edge and outputs it as q. -/
def prob031_dff {dom : DomainConfig}
    (d : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  Signal.register 0#1 d

#synthesizeVerilog prob031_dff
