import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test: using BitVec.append instead of cons
def testAppendBool {dom : DomainConfig}
    (q : Signal dom (BitVec 256)) : Signal dom (BitVec 256) :=
  Signal.map (fun (v : BitVec 256) =>
    let c0 : Bool := (BitVec.extractLsb' 0 1 v).zeroExtend 4 == 3#4
    -- Convert Bool to BitVec 1 using zeroExtend on Bool (since Bool is HW bit)
    -- But we can't use if-then-else...
    -- Try: use BitVec.append with extracted 1-bit slices
    let b0 : BitVec 1 := BitVec.extractLsb' 0 1 v  -- just a test
    let b1 : BitVec 1 := BitVec.extractLsb' 1 1 v
    let row0 : BitVec 2 := BitVec.append b1 b0
    row0.zeroExtend 256) q

#synthesizeVerilog testAppendBool
