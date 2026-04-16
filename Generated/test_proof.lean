import Sparkle

-- Prove that the two bit extraction methods are equivalent
theorem bit_extract_equiv_1 (r : BitVec 3) :
    ((r >>> 1) &&& 1 == 1) = (r &&& 2 == 2) := by
  -- Both check if bit 1 of r is set
  ext i
  simp [BitVec.and, BitVec.shiftRight]
  sorry

theorem bit_extract_equiv_2 (r : BitVec 3) :
    ((r >>> 2) &&& 1 == 1) = (r &&& 4 == 4) := by
  -- Both check if bit 2 of r is set
  sorry

#check bit_extract_equiv_1
#check bit_extract_equiv_2
