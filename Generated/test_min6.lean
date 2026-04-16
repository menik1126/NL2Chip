import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test with let inside lambda - but simpler
def test_min6 {dom : DomainConfig}
    (q : Signal dom (BitVec 4)) : Signal dom (BitVec 4) :=
  Signal.map (fun (v : BitVec 4) => v &&& 7#4) q

#synthesizeVerilog test_min6
