import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Named function WITHOUT let binding
private def computeCell2 (v : BitVec 4) : BitVec 4 :=
  v &&& 7#4

def test_named2 {dom : DomainConfig}
    (q : Signal dom (BitVec 4)) : Signal dom (BitVec 4) :=
  Signal.map computeCell2 q

#synthesizeVerilog test_named2
