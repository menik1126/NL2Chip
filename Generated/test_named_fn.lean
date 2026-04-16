import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Named function with let binding
private def computeCell (v : BitVec 4) : BitVec 4 :=
  let x := v &&& 7#4
  x

def test_named {dom : DomainConfig}
    (q : Signal dom (BitVec 4)) : Signal dom (BitVec 4) :=
  Signal.map computeCell q

#synthesizeVerilog test_named
