import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- XNOR gate: outputs 1 when inputs are equal, 0 when different. -/
def prob083_mt2015_q4b {dom : DomainConfig}
    (x y : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  ~~~(x ^^^ y)

#synthesizeVerilog prob083_mt2015_q4b
