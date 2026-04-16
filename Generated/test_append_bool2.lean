import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test: using BitVec.append without Bool
def testAppendBool2 {dom : DomainConfig}
    (q : Signal dom (BitVec 256)) : Signal dom (BitVec 256) :=
  Signal.map (fun (v : BitVec 256) =>
    let b0 : BitVec 1 := BitVec.extractLsb' 0 1 v
    let b1 : BitVec 1 := BitVec.extractLsb' 1 1 v
    let row0 : BitVec 2 := BitVec.append b1 b0
    row0.zeroExtend 256) q

#synthesizeVerilog testAppendBool2
