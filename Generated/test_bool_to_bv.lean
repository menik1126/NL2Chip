import Sparkle

#check Bool.toUInt8
#check cond

-- Use pattern matching
def Bool.toBitVec1 (b : Bool) : BitVec 1 :=
  match b with
  | true => 1#1
  | false => 0#1

#check Bool.toBitVec1
#eval Bool.toBitVec1 true
#eval Bool.toBitVec1 false
