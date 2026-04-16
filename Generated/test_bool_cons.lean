import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test: Bool from comparison used in BitVec.cons
def testBoolCons {dom : DomainConfig}
    (q : Signal dom (BitVec 256)) : Signal dom (BitVec 256) :=
  Signal.map (fun (v : BitVec 256) =>
    let c0 : Bool := (BitVec.extractLsb' 0 1 v).zeroExtend 4 == 3#4
    let c1 : Bool := (BitVec.extractLsb' 1 1 v).zeroExtend 4 == 3#4
    let row0 : BitVec 2 := BitVec.cons c1 (BitVec.cons c0 BitVec.nil)
    row0.zeroExtend 256) q

#synthesizeVerilog testBoolCons
