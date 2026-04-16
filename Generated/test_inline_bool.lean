import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Can we use Bool result inline without let binding?
-- Try: compute something that uses the comparison result directly

-- The key: if we need to convert Bool->BitVec, we can use
-- Bool's natural 1-bit hardware representation
-- The synthesizer says Bool is .bit (1 bit)
-- Can we zeroExtend a Bool to BitVec 1? Or cast it?

-- Let's try using Signal.map with Bool intermediates but
-- expressing the final result via BitVec ops only

-- What functions in Lean4 std work here?
-- BitVec.replicate : Nat -> BitVec n -> BitVec (n * k)?
-- No...
-- 
-- What about: instead of Bool, keep everything as BitVec 4
-- and use the comparison result as a mask?

def testInlineBool {dom : DomainConfig}
    (q : Signal dom (BitVec 256)) : Signal dom (BitVec 256) :=
  Signal.map (fun (v : BitVec 256) =>
    -- Try computing the GoL step for 1 cell, returning BitVec 1
    -- without Bool intermediate
    let n0 : BitVec 4 := (BitVec.extractLsb' 255 1 v).zeroExtend 4
    let n1 : BitVec 4 := (BitVec.extractLsb' 240 1 v).zeroExtend 4
    let n2 : BitVec 4 := (BitVec.extractLsb' 241 1 v).zeroExtend 4
    let n3 : BitVec 4 := (BitVec.extractLsb' 15 1 v).zeroExtend 4
    let n4 : BitVec 4 := (BitVec.extractLsb' 1 1 v).zeroExtend 4
    let n5 : BitVec 4 := (BitVec.extractLsb' 31 1 v).zeroExtend 4
    let n6 : BitVec 4 := (BitVec.extractLsb' 16 1 v).zeroExtend 4
    let n7 : BitVec 4 := (BitVec.extractLsb' 17 1 v).zeroExtend 4
    let cell : BitVec 4 := (BitVec.extractLsb' 0 1 v).zeroExtend 4
    let sum8 : BitVec 4 := n0 + n1 + n2 + n3 + n4 + n5 + n6 + n7
    let rule_in : BitVec 4 := (sum8 &&& 7#4) ||| cell
    -- rule_in == 3 gives Bool
    -- Can we use `rule_in == 3#4` without if-then-else?
    -- What if the synthesizer can handle Bool as a hardware port directly?
    -- Let's try: the BEq result IS a hardware signal (1-bit Bool)
    -- and we can use Bool.bif or similar
    -- Actually: let's try zeroExtend on Bool (since Bool is a 1-bit type in HW)
    -- This requires a Lean API that converts Bool -> BitVec 1
    -- But all such conversions use if-then-else internally...
    
    -- Alternative: test if the == result can be used directly
    -- since Bool IS a hardware type, maybe we can just do:
    -- let result : Bool := rule_in == 3#4
    -- and then the final expression uses Bool as the last thing
    
    -- But our outer function needs BitVec 256...
    -- what if we zeroExtend the Bool wire?
    rule_in.zeroExtend 256  -- placeholder
  ) q

#synthesizeVerilog testInlineBool
