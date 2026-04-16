import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test: map on 256-bit signal with if expression
def testMapGolIf {dom : DomainConfig}
    (q : Signal dom (BitVec 256)) : Signal dom (BitVec 256) :=
  Signal.map (fun (v : BitVec 256) =>
    let c0 : Bool := (BitVec.extractLsb' 0 1 v).zeroExtend 4 == 3#4
    let b0 : BitVec 1 := if c0 then 1#1 else 0#1
    b0.zeroExtend 256) q

#synthesizeVerilog testMapGolIf
