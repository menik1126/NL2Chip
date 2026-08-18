import Sparkle.Library.RTL

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Library.RTL

private def check (label : String) (ok : Bool) : IO Unit :=
  if ok then
    pure ()
  else
    throw <| IO.userError s!"signed helper Lean simulation failed: {label}"

private def at0 {alpha : Type} (signal : Signal defaultDomain alpha) : alpha :=
  signal.atTime 0

/-- Kept byte-for-byte equivalent to the signed fixed-point skill example so
    prompt guidance remains checked by the Lean simulation regression. -/
private def exampleQ4Multiply {dom : DomainConfig}
    (lhs rhs : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  let product : Signal dom (BitVec 16) := signedMulWide lhs rhs
  let scaled : Signal dom (BitVec 16) :=
    arithShiftRightC product (BitVec.ofNat 16 4)
  let clamped : Signal dom (BitVec 16) :=
    signedSaturateC scaled (BitVec.ofNat 16 65408) (BitVec.ofNat 16 127)
  trunc clamped

def runSignedHelperLeanSimulation : IO Unit := do
  let neg3 : Signal defaultDomain (BitVec 4) := Signal.pure (BitVec.ofNat 4 13)
  let neg2 : Signal defaultDomain (BitVec 4) := Signal.pure (BitVec.ofNat 4 14)
  let pos2 : Signal defaultDomain (BitVec 4) := Signal.pure (BitVec.ofNat 4 2)
  let pos4 : Signal defaultDomain (BitVec 4) := Signal.pure (BitVec.ofNat 4 4)
  let shift1 : Signal defaultDomain (BitVec 4) := Signal.pure (BitVec.ofNat 4 1)
  let shift2 : Signal defaultDomain (BitVec 8) := Signal.pure (BitVec.ofNat 8 2)

  let extended : Signal defaultDomain (BitVec 8) := signExtend neg3
  check "signExtend" (at0 extended == BitVec.ofNat 8 253)
  check "arithShiftRight" (at0 (arithShiftRight neg3 shift1) == BitVec.ofNat 4 14)
  check "arithShiftRightC" (at0 (arithShiftRightC neg3 (BitVec.ofNat 4 1)) == BitVec.ofNat 4 14)
  check "signedLT true" (at0 (signedLT neg3 pos2))
  check "signedLT false" (!at0 (signedLT pos2 neg3))
  check "signedLE" (at0 (signedLE neg3 neg3))
  check "signedGT" (at0 (signedGT pos2 neg3))
  check "signedGE" (at0 (signedGE pos2 pos2))
  check "signedMulWide" (at0 (signedMulWide neg3 pos2) == BitVec.ofNat 8 250)
  check "signedMulTrunc" (at0 (signedMulTrunc neg3 pos2) == BitVec.ofNat 4 10)
  check "signedMulShiftTrunc"
    (at0 (signedMulShiftTrunc neg3 pos4 shift2) == BitVec.ofNat 4 13)
  check "signedAddTo"
    (at0 (signedAddTo (outW := 8) neg3 pos2) == BitVec.ofNat 8 255)
  check "signedSubTo"
    (at0 (signedSubTo (outW := 8) neg3 pos2) == BitVec.ofNat 8 251)
  check "signedMulTo"
    (at0 (signedMulTo (outW := 8) neg3 pos2) == BitVec.ofNat 8 250)

  let pos13 : Signal defaultDomain (BitVec 4) := Signal.pure (BitVec.ofNat 4 13)
  let zero4 : Signal defaultDomain (BitVec 4) := Signal.pure (BitVec.ofNat 4 0)
  let fallback4 : Signal defaultDomain (BitVec 4) := Signal.pure (BitVec.ofNat 4 9)
  check "unsignedDivOr"
    (at0 (unsignedDivOr pos13 pos2 fallback4) == BitVec.ofNat 4 6)
  check "unsignedDivOr zero"
    (at0 (unsignedDivOr pos13 zero4 fallback4) == BitVec.ofNat 4 9)
  check "signedDivOr toward zero"
    (at0 (signedDivOr neg3 pos2 fallback4) == BitVec.ofNat 4 15)
  check "signedDivOr zero"
    (at0 (signedDivOr neg3 zero4 fallback4) == BitVec.ofNat 4 9)
  check "signedAbsTo"
    (at0 (signedAbsTo (outW := 5) neg3) == BitVec.ofNat 5 3)
  check "signed mean toward zero"
    (at0 (signedMeanTowardZeroTo (workW := 5) (outW := 4) neg3 zero4) ==
      BitVec.ofNat 4 15)
  check "signed mean floor"
    (at0 (signedMeanFloorTo (workW := 5) (outW := 4) neg3 zero4) ==
      BitVec.ofNat 4 14)
  let neg3Wide : Signal defaultDomain (BitVec 5) := signExtend neg3
  let shiftOne5 : Signal defaultDomain (BitVec 5) := Signal.pure (BitVec.ofNat 5 1)
  check "signed power-of-two division toward zero"
    (at0 (signedDivPow2TowardZeroTo (workW := 5) (outW := 4)
      neg3Wide shiftOne5) == BitVec.ofNat 4 15)

  let dotLhs : Signal defaultDomain (BitVec (3 * 4)) :=
    Signal.pure (BitVec.ofNat (3 * 4) 0xF2D)
  let dotRhs : Signal defaultDomain (BitVec (3 * 3)) :=
    Signal.pure (BitVec.ofNat (3 * 3) 0x1F2)
  let dot : Signal defaultDomain (BitVec 10) :=
    signedDotPacked (lanes := 3) (lhsW := 4) (rhsW := 3) dotLhs dotRhs
  let sum : Signal defaultDomain (BitVec 10) :=
    signedSumPacked (lanes := 3) (laneW := 4) dotLhs
  let reversed : Signal defaultDomain (BitVec (3 * 4)) :=
    reversePackedLanes (LANES := 3) dotLhs
  let reversedRhs : Signal defaultDomain (BitVec (3 * 3)) :=
    reversePackedLanes (LANES := 3) dotRhs
  let reversedDot : Signal defaultDomain (BitVec 10) :=
    signedDotPacked (lanes := 3) (lhsW := 4) (rhsW := 3) dotLhs reversedRhs
  check "signed packed dot product" (at0 dot == BitVec.ofNat 10 0x3F7)
  check "signed packed sum" (at0 sum == BitVec.ofNat 10 0x3FE)
  check "packed lane reversal" (at0 reversed == BitVec.ofNat 12 0xD2F)
  check "signed reversed-lane dot product"
    (at0 reversedDot == BitVec.ofNat 10 0x3FD)

  let lower : Signal defaultDomain (BitVec 8) := Signal.pure (BitVec.ofNat 8 246)
  let upper : Signal defaultDomain (BitVec 8) := Signal.pure (BitVec.ofNat 8 10)
  let high : Signal defaultDomain (BitVec 8) := Signal.pure (BitVec.ofNat 8 100)
  let low : Signal defaultDomain (BitVec 8) := Signal.pure (BitVec.ofNat 8 156)
  let middle : Signal defaultDomain (BitVec 8) := Signal.pure (BitVec.ofNat 8 5)
  let q4Lhs : Signal defaultDomain (BitVec 8) := Signal.pure (BitVec.ofNat 8 253)
  let q4Rhs : Signal defaultDomain (BitVec 8) := Signal.pure (BitVec.ofNat 8 2)
  check "signedSaturate upper" (at0 (signedSaturate high lower upper) == BitVec.ofNat 8 10)
  check "signedSaturate lower" (at0 (signedSaturate low lower upper) == BitVec.ofNat 8 246)
  check "signedSaturate in range" (at0 (signedSaturate middle lower upper) == BitVec.ofNat 8 5)
  check "signedSaturateC" (at0 (signedSaturateC high (BitVec.ofNat 8 246) (BitVec.ofNat 8 10)) == BitVec.ofNat 8 10)
  check "negative multiply" (at0 (signedMulWide neg3 neg2) == BitVec.ofNat 8 6)
  check "skill fixed-point example" (at0 (exampleQ4Multiply q4Lhs q4Rhs) == BitVec.ofNat 8 255)
  IO.println "SIGNED_HELPER_LEAN_SIM_PASS"

#eval runSignedHelperLeanSimulation
