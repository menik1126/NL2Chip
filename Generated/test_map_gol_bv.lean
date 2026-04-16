import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test: use only BitVec operations, no Bool, no if-then-else
def testMapGolBv {dom : DomainConfig}
    (q : Signal dom (BitVec 256)) : Signal dom (BitVec 256) :=
  Signal.map (fun (v : BitVec 256) =>
    -- Compute neighbor sum as 4-bit
    let s0 : BitVec 4 := (BitVec.extractLsb' 255 1 v).zeroExtend 4 + (BitVec.extractLsb' 240 1 v).zeroExtend 4
    -- Apply rule: (s0 & 7) | cell == 3 -> map to 1 if true
    -- We need to convert Bool -> BitVec 1 without if-then-else
    -- Trick: use BitVec arithmetic: (s0 & 7) | cell == 3 gives a Bool
    -- But we need BitVec...
    -- Option: (BitVec.ofBool b) where b is the comparison result
    let cell0 : BitVec 4 := (BitVec.extractLsb' 0 1 v).zeroExtend 4
    let rule0 : BitVec 1 := BitVec.ofBool ((s0 &&& 7#4 ||| cell0) == 3#4)
    rule0.zeroExtend 256) q

#synthesizeVerilog testMapGolBv
