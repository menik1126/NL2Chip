import Sparkle

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def signedHelperSignExtend {dom : DomainConfig} {W : Nat}
    (x : Signal dom (BitVec W)) : Signal dom (BitVec (W + 4)) :=
  Sparkle.Library.RTL.signExtend x

def signedHelperSignExtend4 {dom : DomainConfig}
    (x : Signal dom (BitVec 4)) : Signal dom (BitVec 8) :=
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

def signedHelperMulWide4 {dom : DomainConfig}
    (lhs rhs : Signal dom (BitVec 4)) : Signal dom (BitVec 8) :=
  Sparkle.Library.RTL.signedMulWide lhs rhs

def signedHelperSaturate {dom : DomainConfig} {W : Nat}
    (value lower upper : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  Sparkle.Library.RTL.signedSaturate value lower upper

def signedHelperUnsignedDivOr {dom : DomainConfig} {W : Nat}
    (numerator denominator fallback : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  Sparkle.Library.RTL.unsignedDivOr numerator denominator fallback

def signedHelperSignedDivOr {dom : DomainConfig} {W : Nat}
    (numerator denominator fallback : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  Sparkle.Library.RTL.signedDivOr numerator denominator fallback

def signedHelperDotPacked {dom : DomainConfig}
    {LANES LHSW RHSW ACCW : Nat}
    (lhs : Signal dom (BitVec (LANES * LHSW)))
    (rhs : Signal dom (BitVec (LANES * RHSW))) : Signal dom (BitVec ACCW) :=
  Sparkle.Library.RTL.signedDotPacked
    (lanes := LANES) (lhsW := LHSW) (rhsW := RHSW) lhs rhs
