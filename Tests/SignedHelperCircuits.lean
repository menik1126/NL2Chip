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

def signedHelperAddTo {dom : DomainConfig} {W : Nat}
    (lhs rhs : Signal dom (BitVec W)) : Signal dom (BitVec (2 * W)) :=
  Sparkle.Library.RTL.signedAddTo lhs rhs

def signedHelperSubTo {dom : DomainConfig} {W : Nat}
    (lhs rhs : Signal dom (BitVec W)) : Signal dom (BitVec (2 * W)) :=
  Sparkle.Library.RTL.signedSubTo lhs rhs

def signedHelperMulTo {dom : DomainConfig} {W : Nat}
    (lhs rhs : Signal dom (BitVec W)) : Signal dom (BitVec (2 * W)) :=
  Sparkle.Library.RTL.signedMulTo lhs rhs

def signedHelperUnsignedDivOr {dom : DomainConfig} {W : Nat}
    (numerator denominator fallback : Signal dom (BitVec W))
    : Signal dom (BitVec W) :=
  Sparkle.Library.RTL.unsignedDivOr numerator denominator fallback

def signedHelperSignedDivOr {dom : DomainConfig} {W : Nat}
    (numerator denominator fallback : Signal dom (BitVec W))
    : Signal dom (BitVec W) :=
  Sparkle.Library.RTL.signedDivOr numerator denominator fallback

def signedHelperAbsTo {dom : DomainConfig} {W : Nat}
    (value : Signal dom (BitVec W)) : Signal dom (BitVec (W + 1)) :=
  Sparkle.Library.RTL.signedAbsTo value

def signedHelperMeanTowardZero {dom : DomainConfig} {W : Nat}
    (lhs rhs : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  Sparkle.Library.RTL.signedMeanTowardZeroTo (workW := W + 1) lhs rhs

def signedHelperMeanFloor {dom : DomainConfig} {W : Nat}
    (lhs rhs : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  Sparkle.Library.RTL.signedMeanFloorTo (workW := W + 1) lhs rhs

def signedHelperDivPow2TowardZero {dom : DomainConfig} {W : Nat}
    (value : Signal dom (BitVec W))
    (amount : Signal dom (BitVec (W + 1))) : Signal dom (BitVec W) :=
  Sparkle.Library.RTL.signedDivPow2TowardZeroTo
    (workW := W + 1) value amount

def signedHelperDotPacked {dom : DomainConfig}
    {LANES LHSW RHSW ACCW : Nat}
    (lhs : Signal dom (BitVec (LANES * LHSW)))
    (rhs : Signal dom (BitVec (LANES * RHSW))) : Signal dom (BitVec ACCW) :=
  Sparkle.Library.RTL.signedDotPacked
    (lanes := LANES) (lhsW := LHSW) (rhsW := RHSW) lhs rhs

def signedHelperSumPacked {dom : DomainConfig}
    {LANES W ACCW : Nat}
    (values : Signal dom (BitVec (LANES * W))) : Signal dom (BitVec ACCW) :=
  Sparkle.Library.RTL.signedSumPacked (lanes := LANES) (laneW := W) values

def signedHelperReversePackedLanes {dom : DomainConfig}
    {LANES W : Nat}
    (values : Signal dom (BitVec (LANES * W)))
    : Signal dom (BitVec (LANES * W)) :=
  Sparkle.Library.RTL.reversePackedLanes (LANES := LANES) values

def signedHelperReversedDotPacked {dom : DomainConfig}
    {LANES LHSW RHSW ACCW : Nat}
    (lhs : Signal dom (BitVec (LANES * LHSW)))
    (rhs : Signal dom (BitVec (LANES * RHSW))) : Signal dom (BitVec ACCW) :=
  let reversedRhs :=
    Sparkle.Library.RTL.reversePackedLanes (LANES := LANES) rhs
  Sparkle.Library.RTL.signedDotPacked
    (lanes := LANES) (lhsW := LHSW) (rhsW := RHSW) lhs reversedRhs

/-- Generic symbolic-width registered DSP stage. This is deliberately not a
    benchmark FSM: it exercises width-derived signed arithmetic, enable/hold,
    explicit reset, and tuple feedback in one reusable regression. -/
def signedHelperRegisteredStage {dom : DomainConfig} {W : Nat}
    (reset enable : Signal dom Bool)
    (lhs rhs bias : Signal dom (BitVec W))
    : Signal dom (BitVec (2 * W) × BitVec (W + 1) × Bool) :=
  let product : Signal dom (BitVec (2 * W)) :=
    Sparkle.Library.RTL.signedMulTo lhs rhs
  let lhsWide : Signal dom (BitVec (W + 1)) :=
    Sparkle.Library.RTL.signExtend lhs
  let biasWide : Signal dom (BitVec (W + 1)) :=
    Sparkle.Library.RTL.signExtend bias
  let shifted : Signal dom (BitVec (W + 1)) :=
    Sparkle.Library.RTL.arithShiftRightC lhsWide (BitVec.ofNat (W + 1) 2)
  let sum : Signal dom (BitVec (W + 1)) := biasWide + shifted
  Signal.loop fun state =>
    let heldProduct := Signal.mux enable product state.fst
    let heldSum := Signal.mux enable sum state.snd.fst
    let nextProduct := Sparkle.Library.RTL.resetHigh
      (BitVec.ofNat (2 * W) 0) reset heldProduct
    let nextSum := Sparkle.Library.RTL.resetHigh
      (BitVec.ofNat (W + 1) 0) reset heldSum
    let nextValid := Sparkle.Library.RTL.resetHigh false reset enable
    Signal.register
      (BitVec.ofNat (2 * W) 0, BitVec.ofNat (W + 1) 0, false)
      (bundle2 nextProduct (bundle2 nextSum nextValid))
