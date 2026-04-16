import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Minimal: just a simple computation with zeroExtend
def test_min {dom : DomainConfig}
    (q : Signal dom (BitVec 256)) : Signal dom (BitVec 256) :=
  Signal.map (fun (v : BitVec 256) =>
    let sum : BitVec 4 := (BitVec.extractLsb' 0 1 v).zeroExtend 4
    let result : BitVec 4 := sum &&& 7#4
    result.zeroExtend 256) q

#synthesizeVerilog test_min
