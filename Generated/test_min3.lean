import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Super minimal with extractLsb' inside Signal.map
def test_min3 {dom : DomainConfig}
    (q : Signal dom (BitVec 4)) : Signal dom (BitVec 1) :=
  Signal.map (BitVec.extractLsb' 0 1) q

#synthesizeVerilog test_min3
