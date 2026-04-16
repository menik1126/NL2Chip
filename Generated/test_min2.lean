import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Without zeroExtend to 256
def test_min2 {dom : DomainConfig}
    (q : Signal dom (BitVec 4)) : Signal dom (BitVec 4) :=
  Signal.map (fun (v : BitVec 4) =>
    let sum : BitVec 4 := (BitVec.extractLsb' 0 1 v).zeroExtend 4
    let result : BitVec 4 := sum &&& 7#4
    result) q

#synthesizeVerilog test_min2
