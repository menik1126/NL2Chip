import Sparkle.Library.RTL

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Library.RTL

private def at0 {alpha : Type} (signal : Signal defaultDomain alpha) : alpha :=
  signal.atTime 0

private def check (label : String) (ok : Bool) : IO Unit :=
  if ok then pure () else throw <| IO.userError s!"signed helper smoke failed: {label}"

def runSignedHelperSmoke : IO Unit := do
  let neg3 : Signal defaultDomain (BitVec 4) := Signal.pure (BitVec.ofNat 4 13)
  let neg2 : Signal defaultDomain (BitVec 4) := Signal.pure (BitVec.ofNat 4 14)
  let pos2 : Signal defaultDomain (BitVec 4) := Signal.pure (BitVec.ofNat 4 2)
  let shift1 : Signal defaultDomain (BitVec 4) := Signal.pure (BitVec.ofNat 4 1)
  let shift2 : Signal defaultDomain (BitVec 8) := Signal.pure (BitVec.ofNat 8 2)
  check "signExtend" (at0 (signExtend (outW := 8) neg3) == BitVec.ofNat 8 253)
  check "arithShiftRight" (at0 (arithShiftRight neg3 shift1) == BitVec.ofNat 4 14)
  check "signedLT" (at0 (signedLT neg3 pos2))
  check "signedLE" (at0 (signedLE neg3 neg3))
  check "signedGT" (at0 (signedGT pos2 neg3))
  check "signedGE" (at0 (signedGE pos2 pos2))
  check "signedAddTo" (at0 (signedAddTo (outW := 8) neg3 pos2) == BitVec.ofNat 8 255)
  check "signedSubTo" (at0 (signedSubTo (outW := 8) neg3 pos2) == BitVec.ofNat 8 251)
  check "signedMulWide" (at0 (signedMulWide neg3 pos2) == BitVec.ofNat 8 250)
  check "signedMulTrunc" (at0 (signedMulTrunc neg3 pos2) == BitVec.ofNat 4 10)
  check "signedMulShiftTrunc"
    (at0 (signedMulShiftTrunc neg3 neg2 shift2) == BitVec.ofNat 4 1)
  let lower : Signal defaultDomain (BitVec 8) := Signal.pure (BitVec.ofNat 8 246)
  let upper : Signal defaultDomain (BitVec 8) := Signal.pure (BitVec.ofNat 8 10)
  let high : Signal defaultDomain (BitVec 8) := Signal.pure (BitVec.ofNat 8 100)
  check "signedSaturate" (at0 (signedSaturate high lower upper) == BitVec.ofNat 8 10)
  IO.println "E83_SIGNED_HELPER_SMOKE_PASS"

def main : IO Unit := runSignedHelperSmoke
