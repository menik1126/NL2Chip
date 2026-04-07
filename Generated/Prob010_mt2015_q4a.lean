import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Boolean function z = (x^y) & x: XOR then AND with x. -/
def prob010_mt2015_q4a {dom : DomainConfig}
    (x y : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  (x ^^^ y) &&& x

#synthesizeVerilog prob010_mt2015_q4a