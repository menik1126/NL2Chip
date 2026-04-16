import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test: BitVec 4 sum with 8 addends
def test4bitSum8 {dom : DomainConfig}
    (q : Signal dom (BitVec 256)) : Signal dom (BitVec 256) :=
  Signal.map (fun (v : BitVec 256) =>
    let n0 : BitVec 4 := (BitVec.extractLsb' 255 1 v).zeroExtend 4
    let n1 : BitVec 4 := (BitVec.extractLsb' 240 1 v).zeroExtend 4
    let n2 : BitVec 4 := (BitVec.extractLsb' 241 1 v).zeroExtend 4
    let n3 : BitVec 4 := (BitVec.extractLsb' 15 1 v).zeroExtend 4
    let n4 : BitVec 4 := (BitVec.extractLsb' 1 1 v).zeroExtend 4
    let n5 : BitVec 4 := (BitVec.extractLsb' 31 1 v).zeroExtend 4
    let n6 : BitVec 4 := (BitVec.extractLsb' 16 1 v).zeroExtend 4
    let n7 : BitVec 4 := (BitVec.extractLsb' 17 1 v).zeroExtend 4
    let sum8 : BitVec 4 := n0 + n1 + n2 + n3 + n4 + n5 + n6 + n7
    sum8.zeroExtend 256) q

#synthesizeVerilog test4bitSum8
