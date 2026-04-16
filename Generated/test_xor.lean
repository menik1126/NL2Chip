import Sparkle

#check BitVec.toNat
#check Nat.testBit

-- Check if value's popcount is odd
def xorReduce (n : Nat) (x : BitVec n) : Bool :=
  -- XOR of all bits = parity of popcount
  -- But there's no popcount... let me try a different approach
  (x.toNat % 2) == 1

#eval xorReduce 4 0b0000  -- false (0 ones)
#eval xorReduce 4 0b0001  -- true (1 one)
#eval xorReduce 4 0b0011  -- false (2 ones)
#eval xorReduce 4 0b0111  -- true (3 ones)
#eval xorReduce 4 0b1111  -- false (4 ones)
