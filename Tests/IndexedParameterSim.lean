import Tests.IndexedParameterCircuits

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Library.RTL

private def check (label : String) (ok : Bool) : IO Unit :=
  if ok then pure ()
  else throw <| IO.userError s!"indexed parameter Lean simulation failed: {label}"

private def at0 {alpha : Type} (signal : Signal defaultDomain alpha) : alpha :=
  signal.atTime 0

def runIndexedParameterLeanSimulation : IO Unit := do
  let data4 : Signal defaultDomain (BitVec 4) := Signal.pure (BitVec.ofNat 4 0xB)
  let data17 : Signal defaultDomain (BitVec 17) := Signal.pure (BitVec.ofNat 17 0x15555)
  let scattered : Signal defaultDomain (BitVec 8) :=
    indexedScatter (DATAW := 4) (PARITYW := 3) data4
  let parity : Signal defaultDomain (BitVec 3) :=
    indexedParity (W := 8) (PARITYW := 3) scattered
  let placed : Signal defaultDomain (BitVec 8) :=
    indexedPlaceParity (DATAW := 4) (PARITYW := 3) scattered parity
  let encoded : Signal defaultDomain (BitVec 8) :=
    indexedHammingEncode (DATAW := 4) (PARITYW := 3) data4
  let corrupted : Signal defaultDomain (BitVec 8) :=
    Signal.pure (BitVec.ofNat 8 0xA2)
  let chunkInput : Signal defaultDomain (BitVec 8) :=
    Signal.pure (BitVec.ofNat 8 0)

  check "generateBitsWithIndex" (at0 (indexedGenerateIdentity data17) == BitVec.ofNat 17 0x15555)
  check "generateChunksWithIndex"
    (at0 (indexedGenerateChunks (N := 5) chunkInput) == BitVec.ofNat 20 0x43210)
  check "scatterNonPowerOfTwoBits" (at0 scattered == BitVec.ofNat 8 0xA8)
  check "parityByIndexMask" (at0 parity == BitVec.ofNat 3 0x1)
  check "placeParityBits" (at0 placed == BitVec.ofNat 8 0xAA)
  check "gatherNonPowerOfTwoBits"
    (at0 (indexedGather (DATAW := 4) (PARITYW := 3) placed) == BitVec.ofNat 4 0xB)
  check "round trip"
    (at0 (indexedRoundTrip (DATAW := 4) (PARITYW := 3) data4) == BitVec.ofNat 4 0xB)
  check "Hamming encode" (at0 encoded == BitVec.ofNat 8 0xAA)
  check "Hamming single-error correction"
    (at0 (indexedHammingDecode (DATAW := 4) (PARITYW := 3) corrupted) ==
      BitVec.ofNat 4 0xB)
  IO.println "INDEXED_PARAMETER_LEAN_SIM_PASS"

#eval! runIndexedParameterLeanSimulation
