import Sparkle

#check BitVec.getLsb

-- Test BitVec bit access
#eval (0b11010110 : BitVec 8).getLsb ⟨2, by omega⟩

-- Function for 256-to-1 mux
def extractBitFromBitVec (input : BitVec 256) (sel : BitVec 8) : BitVec 1 :=
  if h : sel.toNat < 256 then
    if input.getLsb ⟨sel.toNat, h⟩ then 1#1 else 0#1
  else 
    0#1