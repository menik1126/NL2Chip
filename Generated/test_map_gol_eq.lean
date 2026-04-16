import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test: use BEq.beq result directly
def testMapGolEq {dom : DomainConfig}
    (q : Signal dom (BitVec 256)) : Signal dom (BitVec 256) :=
  Signal.map (fun (v : BitVec 256) =>
    let s0 : BitVec 4 := (BitVec.extractLsb' 255 1 v).zeroExtend 4 + (BitVec.extractLsb' 240 1 v).zeroExtend 4
    let cell0 : BitVec 4 := (BitVec.extractLsb' 0 1 v).zeroExtend 4
    -- The comparison (s0 &&& 7#4 ||| cell0) == 3#4 gives Bool
    -- We need to convert to BitVec 1
    -- Try: use decide or toUInt8 or similar
    let result : BitVec 4 := s0 + cell0
    result.zeroExtend 256) q

#synthesizeVerilog testMapGolEq
