import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test: extractLsb' and zeroExtend inside let
def test_ez {dom : DomainConfig}
    (q : Signal dom (BitVec 8)) : Signal dom (BitVec 4) :=
  Signal.map (fun (v : BitVec 8) =>
    let bit0 : BitVec 1 := BitVec.extractLsb' 0 1 v
    let bit1 : BitVec 1 := BitVec.extractLsb' 1 1 v
    let sum : BitVec 4 := bit0.zeroExtend 4 + bit1.zeroExtend 4
    sum &&& (7 : BitVec 4)) q

#synthesizeVerilog test_ez
