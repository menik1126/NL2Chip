import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test with · notation -/
def prob092_test4 {dom : DomainConfig}
    (inp : Signal dom (BitVec 100))
    : Signal dom (BitVec 100) :=
  
  inp.map (· >>> 1)

#synthesizeVerilog prob092_test4
