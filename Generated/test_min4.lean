import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test zeroExtend inside Signal.map  
def test_min4 {dom : DomainConfig}
    (q : Signal dom (BitVec 4)) : Signal dom (BitVec 8) :=
  Signal.map (fun v => (v : BitVec 4).zeroExtend 8) q

#synthesizeVerilog test_min4
