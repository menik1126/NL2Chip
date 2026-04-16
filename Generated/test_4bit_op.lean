import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test: BitVec 4 sum with many addends
def test4bitOp {dom : DomainConfig}
    (q : Signal dom (BitVec 256)) : Signal dom (BitVec 256) :=
  Signal.map (fun (v : BitVec 256) =>
    let n0 : BitVec 4 := (BitVec.extractLsb' 255 1 v).zeroExtend 4
    let n1 : BitVec 4 := (BitVec.extractLsb' 240 1 v).zeroExtend 4
    let sum : BitVec 4 := n0 + n1
    sum.zeroExtend 256) q

#synthesizeVerilog test4bitOp
