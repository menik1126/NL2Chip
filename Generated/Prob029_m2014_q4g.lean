import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Two-input XNOR followed by XOR with third input. -/
def prob029_m2014_q4g {dom : DomainConfig}
    (in1 in2 in3 : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  let xnor_result := ~~~(in1 ^^^ in2)
  xnor_result ^^^ in3

#synthesizeVerilog prob029_m2014_q4g
