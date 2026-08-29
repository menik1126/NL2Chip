import Sparkle

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Parameterized signed helper circuits used by the native SV and CppSim
    regression suites. They intentionally exercise widths such as W = 4,
    where host integer casts cannot stand in for bit-vector signedness. -/
def signedHelperSignExtend {dom : DomainConfig} {W : Nat}
    (x : Signal dom (BitVec W)) : Signal dom (BitVec (W + 4)) :=
  Sparkle.Library.RTL.signExtend x

def signedHelperArithmeticShiftRight {dom : DomainConfig} {W : Nat}
    (x amount : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  Sparkle.Library.RTL.arithShiftRight x amount

def signedHelperLT {dom : DomainConfig} {W : Nat}
    (lhs rhs : Signal dom (BitVec W)) : Signal dom Bool :=
  Sparkle.Library.RTL.signedLT lhs rhs

def signedHelperMulWide {dom : DomainConfig} {W : Nat}
    (lhs rhs : Signal dom (BitVec W)) : Signal dom (BitVec (W + W)) :=
  Sparkle.Library.RTL.signedMulWide lhs rhs

def signedHelperMulTrunc {dom : DomainConfig} {W : Nat}
    (lhs rhs : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  Sparkle.Library.RTL.signedMulTrunc lhs rhs

def signedHelperMulShiftTrunc {dom : DomainConfig} {W : Nat}
    (lhs rhs : Signal dom (BitVec W))
    (amount : Signal dom (BitVec (W + W))) : Signal dom (BitVec W) :=
  Sparkle.Library.RTL.signedMulShiftTrunc lhs rhs amount

def signedHelperSaturate {dom : DomainConfig} {W : Nat}
    (value lower upper : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  Sparkle.Library.RTL.signedSaturate value lower upper

def signedHelperSaturateC8 {dom : DomainConfig}
    (value : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  Sparkle.Library.RTL.signedSaturateC value
    (BitVec.ofNat 8 246) (BitVec.ofNat 8 10)
