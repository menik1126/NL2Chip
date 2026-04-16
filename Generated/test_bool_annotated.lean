import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test: explicit type annotations inside lambda
def testBoolAnnotated {dom : DomainConfig}
    (q : Signal dom (BitVec 256)) : Signal dom (BitVec 256) :=
  Signal.map (fun (v : BitVec 256) =>
    let sum : BitVec 4 := (BitVec.extractLsb' 0 1 v).zeroExtend 4 + (BitVec.extractLsb' 1 1 v).zeroExtend 4
    -- Try: use comparison as BitVec 1 via extract from a concatenation
    -- Instead of Bool, work only with BitVec
    -- Rule: sum_masked | cell == 3
    let sum_masked : BitVec 4 := sum &&& (7 : BitVec 4)
    let cell : BitVec 4 := (BitVec.extractLsb' 0 1 v).zeroExtend 4
    let test_val : BitVec 4 := sum_masked ||| cell
    -- Check == 3: this gives Bool...
    -- Try: subtract 3 and check if == 0 
    -- Still gives Bool
    -- Alternative: use XOR and NOT to get BitVec 1
    -- (test_val XOR 3) == 0 means test_val == 3
    -- How to convert "== 0" to BitVec 1?
    -- 
    -- In hardware: a == b generates a 1-bit signal directly
    -- The problem is that Lean's BEq.beq returns Bool, not BitVec 1
    -- 
    -- SOLUTION: Keep using BitVec only. The comparison will be inlined.
    -- Use only zeroExtend 256 from BitVec 4 to get the test value in 256 bits.
    -- The lower 4 bits will show the test_val.
    test_val.zeroExtend 256) q

#synthesizeVerilog testBoolAnnotated
