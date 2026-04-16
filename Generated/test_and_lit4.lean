import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test: zeroExtend from 4-bit to 8-bit (not 256)
def testAndLit4 {dom : DomainConfig}
    (q : Signal dom (BitVec 256)) : Signal dom (BitVec 8) :=
  Signal.map (fun (v : BitVec 256) =>
    let n0 : BitVec 4 := (BitVec.extractLsb' 0 1 v).zeroExtend 4
    let masked : BitVec 4 := n0 &&& 7#4
    masked.zeroExtend 8) q

#synthesizeVerilog testAndLit4
