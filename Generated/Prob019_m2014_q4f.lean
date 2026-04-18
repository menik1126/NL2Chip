import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- AND gate with inverted second input: out = in1 & ~in2 -/
def prob019_m2014_q4f {dom : DomainConfig}
    (in1 in2 : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  in1 &&& (~~~in2)

#synthesizeVerilog prob019_m2014_q4f
