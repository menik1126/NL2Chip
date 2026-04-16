import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test with just BitVec.append
private def conwayStep (v : BitVec 256) : BitVec 256 :=
  let c0 : Bool := ((((BitVec.extractLsb' 255 1 v).zeroExtend 4 + (BitVec.extractLsb' 240 1 v).zeroExtend 4 + (BitVec.extractLsb' 241 1 v).zeroExtend 4 + (BitVec.extractLsb' 15 1 v).zeroExtend 4 + (BitVec.extractLsb' 1 1 v).zeroExtend 4 + (BitVec.extractLsb' 31 1 v).zeroExtend 4 + (BitVec.extractLsb' 16 1 v).zeroExtend 4 + (BitVec.extractLsb' 17 1 v).zeroExtend 4) &&& 7#4) ||| (BitVec.extractLsb' 0 1 v).zeroExtend 4) == 3#4
  let c1 : Bool := ((((BitVec.extractLsb' 240 1 v).zeroExtend 4 + (BitVec.extractLsb' 241 1 v).zeroExtend 4 + (BitVec.extractLsb' 242 1 v).zeroExtend 4 + (BitVec.extractLsb' 0 1 v).zeroExtend 4 + (BitVec.extractLsb' 2 1 v).zeroExtend 4 + (BitVec.extractLsb' 16 1 v).zeroExtend 4 + (BitVec.extractLsb' 17 1 v).zeroExtend 4 + (BitVec.extractLsb' 18 1 v).zeroExtend 4) &&& 7#4) ||| (BitVec.extractLsb' 1 1 v).zeroExtend 4) == 3#4
  let b0 : BitVec 1 := if c0 then 1#1 else 0#1
  let b1 : BitVec 1 := if c1 then 1#1 else 0#1
  let result : BitVec 2 := BitVec.append b1 b0
  result.zeroExtend 256

def test_circuit2 {dom : DomainConfig}
    (q : Signal dom (BitVec 256)) : Signal dom (BitVec 256) :=
  Signal.map conwayStep q

#synthesizeVerilog test_circuit2
