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

def symbolicGenerateNot {dom : DomainConfig} {W : Nat}
    (x : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  Signal.mapBits (fun bit => Bool.not bit) x

/-- A symbolic-width feedback register. The explicit dff seed fixes the
    loop state type without freezing the retained width W. -/
def symbolicLoopXor {dom : DomainConfig} {W : Nat}
    (x : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  Signal.loop fun q => Signal.register (BitVec.ofNat W 0) (q ^^^ x)

/-- A symbolic derived width used by depth-indexed state such as a FIFO pointer.
    ceiling-log2 DEPTH is preserved as the backend's symbolic clog2 expression. -/
def symbolicDerivedLoop {dom : DomainConfig} {DEPTH : Nat}
    (x : Signal dom (BitVec (Sparkle.Library.RTL.clog2 DEPTH)))
    : Signal dom (BitVec (Sparkle.Library.RTL.clog2 DEPTH)) :=
  Signal.loop fun q =>
    Signal.register (BitVec.ofNat (Sparkle.Library.RTL.clog2 DEPTH) 0) (q ^^^ x)
