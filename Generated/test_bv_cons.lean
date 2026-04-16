import Sparkle
import Sparkle.Compiler.Elab

-- Test BitVec.cons with a small example
def testCons : BitVec 4 :=
  let c0 : Bool := true
  let c1 : Bool := false
  let c2 : Bool := true
  let c3 : Bool := false
  BitVec.cons c3 (BitVec.cons c2 (BitVec.cons c1 (BitVec.cons c0 BitVec.nil)))

#eval testCons  -- should be 0101 = 5
