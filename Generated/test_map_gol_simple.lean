import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test: map on 256-bit signal with simple bit operation
def testMapGolSimple {dom : DomainConfig}
    (q : Signal dom (BitVec 256)) : Signal dom (BitVec 256) :=
  Signal.map (fun (v : BitVec 256) =>
    let b0 : BitVec 1 := BitVec.extractLsb' 0 1 v
    b0.zeroExtend 256) q

#synthesizeVerilog testMapGolSimple
