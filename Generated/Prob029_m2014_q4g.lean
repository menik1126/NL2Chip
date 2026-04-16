import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- XNOR-then-XOR: out = (in1 XNOR in2) XOR in3 = ~(in1 ^ in2) ^ in3 -/
def prob029_m2014_q4g {dom : DomainConfig}
    (in1 in2 in3 : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  let xnor12 := ~~~(in1 ^^^ in2)
  xnor12 ^^^ in3

#synthesizeVerilog prob029_m2014_q4g
