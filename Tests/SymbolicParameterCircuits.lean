import Sparkle

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def symbolicIdentity {dom : DomainConfig} {W : Nat}
    (x : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  x

def symbolicXor {dom : DomainConfig} {W : Nat}
    (lhs rhs : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  lhs ^^^ rhs

def symbolicConcat {dom : DomainConfig} {HI LO : Nat}
    (hi : Signal dom (BitVec HI))
    (lo : Signal dom (BitVec LO)) : Signal dom (BitVec (HI + LO)) :=
  hi ++ lo

def symbolicSliceLow {dom : DomainConfig} {W : Nat}
    (x : Signal dom (BitVec (W + 1))) : Signal dom (BitVec W) :=
  x.map (BitVec.extractLsb' 0 W ·)

def symbolicZeroExtend {dom : DomainConfig} {W : Nat}
    (x : Signal dom (BitVec W)) : Signal dom (BitVec (W + 1)) :=
  x.map (·.zeroExtend (W + 1))

def symbolicRegister {dom : DomainConfig} {W : Nat}
    (x : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  Signal.register (BitVec.ofNat W 1) x

def symbolicMemory {dom : DomainConfig} {ADDR_W DATA_W : Nat}
    (writeAddr : Signal dom (BitVec ADDR_W))
    (writeData : Signal dom (BitVec DATA_W))
    (writeEnable : Signal dom Bool)
    (readAddr : Signal dom (BitVec ADDR_W)) : Signal dom (BitVec DATA_W) :=
  Signal.memory writeAddr writeData writeEnable readAddr

@[sparkle_module]
def symbolicXorChild {dom : DomainConfig} {W : Nat}
    (lhs rhs : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  lhs ^^^ rhs

def symbolicXorHierarchy {dom : DomainConfig} {W : Nat}
    (lhs rhs : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  symbolicXorChild lhs rhs
