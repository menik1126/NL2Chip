import Sparkle

-- Prove for all 8 possible 3-bit values
theorem bit_extract_equiv_1_0 : ((0#3 : BitVec 3) >>> 1) &&& 1 == 1 ↔ (0#3 : BitVec 3) &&& 2 == 2 := by decide
theorem bit_extract_equiv_1_1 : ((1#3 : BitVec 3) >>> 1) &&& 1 == 1 ↔ (1#3 : BitVec 3) &&& 2 == 2 := by decide
theorem bit_extract_equiv_1_2 : ((2#3 : BitVec 3) >>> 1) &&& 1 == 1 ↔ (2#3 : BitVec 3) &&& 2 == 2 := by decide
theorem bit_extract_equiv_1_3 : ((3#3 : BitVec 3) >>> 1) &&& 1 == 1 ↔ (3#3 : BitVec 3) &&& 2 == 2 := by decide
theorem bit_extract_equiv_1_4 : ((4#3 : BitVec 3) >>> 1) &&& 1 == 1 ↔ (4#3 : BitVec 3) &&& 2 == 2 := by decide
theorem bit_extract_equiv_1_5 : ((5#3 : BitVec 3) >>> 1) &&& 1 == 1 ↔ (5#3 : BitVec 3) &&& 2 == 2 := by decide
theorem bit_extract_equiv_1_6 : ((6#3 : BitVec 3) >>> 1) &&& 1 == 1 ↔ (6#3 : BitVec 3) &&& 2 == 2 := by decide
theorem bit_extract_equiv_1_7 : ((7#3 : BitVec 3) >>> 1) &&& 1 == 1 ↔ (7#3 : BitVec 3) &&& 2 == 2 := by decide

#check bit_extract_equiv_1_0
