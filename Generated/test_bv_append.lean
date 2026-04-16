import Sparkle
import Sparkle.Compiler.Elab

-- Test BitVec.append
def testAppend : BitVec 8 :=
  let r0 : BitVec 4 := 0b1010#4
  let r1 : BitVec 4 := 0b0101#4
  BitVec.append r1 r0  -- r1 is MSBs, r0 is LSBs

#eval testAppend  -- should be 0101_1010 = 0x5A = 90
