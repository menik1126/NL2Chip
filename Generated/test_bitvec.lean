import Sparkle

#check BitVec
#check BitVec.allOnes

-- Try to figure out how to do reduction operations
def testAnd (x : BitVec 100) : Bool :=
  x == BitVec.allOnes 100

def testOr (x : BitVec 100) : Bool :=
  x != 0#100

-- For XOR, we need to XOR all bits together (parity)
-- This would require iterating over all bits
