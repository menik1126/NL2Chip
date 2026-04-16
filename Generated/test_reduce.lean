import Sparkle

-- Helper function to compute XOR reduction of all bits in a BitVec
def BitVec.xorReduce {n : Nat} (x : BitVec n) : Bool :=
  let rec loop (i : Nat) (acc : Bool) : Bool :=
    match i with
    | 0 => acc
    | i' + 1 =>
      if h : i' < n then
        loop i' (xor acc (x.getLsb ⟨i', h⟩))
      else
        loop i' acc
  loop n false

-- Helper for AND reduction: all bits are 1
def BitVec.andReduce {n : Nat} (x : BitVec n) : Bool :=
  x == BitVec.allOnes n

-- Helper for OR reduction: at least one bit is 1
def BitVec.orReduce {n : Nat} (x : BitVec n) : Bool :=
  x != 0#n

#check BitVec.xorReduce
#check BitVec.andReduce
#check BitVec.orReduce

-- Test it
#eval BitVec.xorReduce (0b1010 : BitVec 4)  -- Should be false (even parity: 2 ones)
#eval BitVec.xorReduce (0b1011 : BitVec 4)  -- Should be true (odd parity: 3 ones)
#eval BitVec.andReduce (0b1111 : BitVec 4)  -- Should be true
#eval BitVec.andReduce (0b1110 : BitVec 4)  -- Should be false
#eval BitVec.orReduce (0b0000 : BitVec 4)   -- Should be false
#eval BitVec.orReduce (0b0001 : BitVec 4)   -- Should be true
