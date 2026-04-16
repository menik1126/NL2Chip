import Sparkle

#check BitVec.extractLsb'
#check BitVec.getLsb

-- Test extracting single bits using extractLsb' with width 1
example : BitVec 256 → Nat → BitVec 1 := fun v n => BitVec.extractLsb' n 1 v

#eval BitVec.extractLsb' 2 1 (0b11010110 : BitVec 8)