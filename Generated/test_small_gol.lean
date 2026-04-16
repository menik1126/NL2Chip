import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test with just 2 rows to understand if the approach works
private def conwayStep (v : BitVec 256) : BitVec 256 :=
  -- Cell 0 (row 0, col 0)
  let c0 : Bool := ((((BitVec.extractLsb' 255 1 v).zeroExtend 4 + (BitVec.extractLsb' 240 1 v).zeroExtend 4 + (BitVec.extractLsb' 241 1 v).zeroExtend 4 + (BitVec.extractLsb' 15 1 v).zeroExtend 4 + (BitVec.extractLsb' 1 1 v).zeroExtend 4 + (BitVec.extractLsb' 31 1 v).zeroExtend 4 + (BitVec.extractLsb' 16 1 v).zeroExtend 4 + (BitVec.extractLsb' 17 1 v).zeroExtend 4) &&& 7#4) ||| (BitVec.extractLsb' 0 1 v).zeroExtend 4) == 3#4
  -- Cell 1 (row 0, col 1)
  let c1 : Bool := ((((BitVec.extractLsb' 240 1 v).zeroExtend 4 + (BitVec.extractLsb' 241 1 v).zeroExtend 4 + (BitVec.extractLsb' 242 1 v).zeroExtend 4 + (BitVec.extractLsb' 0 1 v).zeroExtend 4 + (BitVec.extractLsb' 2 1 v).zeroExtend 4 + (BitVec.extractLsb' 16 1 v).zeroExtend 4 + (BitVec.extractLsb' 17 1 v).zeroExtend 4 + (BitVec.extractLsb' 18 1 v).zeroExtend 4) &&& 7#4) ||| (BitVec.extractLsb' 1 1 v).zeroExtend 4) == 3#4
  let row0 : BitVec 2 := BitVec.cons c1 (BitVec.cons c0 BitVec.nil)
  row0.zeroExtend 256

def test_circuit {dom : DomainConfig}
    (q : Signal dom (BitVec 256)) : Signal dom (BitVec 256) :=
  Signal.map conwayStep q

#synthesizeVerilog test_circuit
