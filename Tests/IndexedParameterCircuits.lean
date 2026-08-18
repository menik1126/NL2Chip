import Sparkle

open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Library.RTL

@[sparkle_module]
def indexedSelectBit {dom : DomainConfig} {W : Nat}
    (index data : Signal dom (BitVec W)) : Signal dom (BitVec 1) :=
  let shifted : Signal dom (BitVec W) := data >>> index
  Signal.cast shifted

def indexedGenerateIdentity {dom : DomainConfig} {W : Nat}
    (data : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  Signal.generateBitsWithIndex indexedSelectBit data

@[sparkle_module]
def indexedLaneValue {dom : DomainConfig} {INDEXW INW OUTW : Nat}
    (index : Signal dom (BitVec INDEXW))
    (data : Signal dom (BitVec INW)) : Signal dom (BitVec OUTW) :=
  let dataBit : Signal dom (BitVec 1) := Signal.cast data
  let dataWide : Signal dom (BitVec OUTW) := zext dataBit
  let indexWide : Signal dom (BitVec OUTW) := Signal.cast index
  indexWide ^^^ dataWide

def indexedGenerateChunks {dom : DomainConfig} {N : Nat}
    (data : Signal dom (BitVec 8)) : Signal dom (BitVec (N * 4)) :=
  Signal.generateChunksWithIndex (INDEXW := 8) indexedLaneValue data

def indexedScatter {dom : DomainConfig} {DATAW PARITYW : Nat}
    (data : Signal dom (BitVec DATAW))
    : Signal dom (BitVec (DATAW + PARITYW + 1)) :=
  scatterNonPowerOfTwoBits (PARITYW := PARITYW) data

def indexedParity {dom : DomainConfig} {W PARITYW : Nat}
    (value : Signal dom (BitVec W)) : Signal dom (BitVec PARITYW) :=
  parityByIndexMask value

def indexedPlaceParity {dom : DomainConfig} {DATAW PARITYW : Nat}
    (base : Signal dom (BitVec (DATAW + PARITYW + 1)))
    (parity : Signal dom (BitVec PARITYW))
    : Signal dom (BitVec (DATAW + PARITYW + 1)) :=
  placeParityBits base parity

def indexedGather {dom : DomainConfig} {DATAW PARITYW : Nat}
    (encoded : Signal dom (BitVec (DATAW + PARITYW + 1)))
    : Signal dom (BitVec DATAW) :=
  gatherNonPowerOfTwoBits (PARITYW := PARITYW) encoded

def indexedRoundTrip {dom : DomainConfig} {DATAW PARITYW : Nat}
    (data : Signal dom (BitVec DATAW)) : Signal dom (BitVec DATAW) :=
  gatherNonPowerOfTwoBits (PARITYW := PARITYW)
    (scatterNonPowerOfTwoBits (PARITYW := PARITYW) data)

def indexedHammingEncode {dom : DomainConfig} {DATAW PARITYW : Nat}
    (data : Signal dom (BitVec DATAW))
    : Signal dom (BitVec (DATAW + PARITYW + 1)) :=
  let encodedWidth := DATAW + PARITYW + 1
  let scattered : Signal dom (BitVec encodedWidth) :=
    scatterNonPowerOfTwoBits (PARITYW := PARITYW) data
  let parity : Signal dom (BitVec PARITYW) := parityByIndexMask scattered
  let withParity : Signal dom (BitVec encodedWidth) :=
    placeParityBits scattered parity
  let overall : Signal dom (BitVec 1) := bit (popCount withParity) 0
  let overallWide : Signal dom (BitVec encodedWidth) := zext overall
  withParity ||| overallWide

def indexedHammingDecode {dom : DomainConfig} {DATAW PARITYW : Nat}
    (encoded : Signal dom (BitVec (DATAW + PARITYW + 1)))
    : Signal dom (BitVec DATAW) :=
  let encodedWidth := DATAW + PARITYW + 1
  let syndrome : Signal dom (BitVec PARITYW) := parityByIndexMask encoded
  let shiftAmount : Signal dom (BitVec encodedWidth) := Signal.cast syndrome
  let one : Signal dom (BitVec encodedWidth) :=
    Signal.pure (BitVec.ofNat encodedWidth 1)
  let corrected := Signal.mux (nonZero syndrome)
    (encoded ^^^ (one <<< shiftAmount)) encoded
  gatherNonPowerOfTwoBits (PARITYW := PARITYW) corrected
