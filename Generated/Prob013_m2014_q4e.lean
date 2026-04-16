import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 2-input NOR gate: output is high only when both inputs are low. -/
def prob013_m2014_q4e {dom : DomainConfig}
    (in1 in2 : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  ~~~(in1 ||| in2)

#synthesizeVerilog prob013_m2014_q4e
