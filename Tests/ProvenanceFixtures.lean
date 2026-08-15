import Sparkle
import Sparkle.Library.RTL

open Sparkle.Core.Domain
open Sparkle.Core.Signal

namespace Tests.ProvenanceFixtures

def namedLoop (seed : Signal Domain (BitVec 8)) : Signal Domain (BitVec 8) :=
  let state := Signal.loop fun feedback => seed + feedback
  state

def fvarAlias (a : Signal Domain (BitVec 8)) : Signal Domain (BitVec 8) :=
  let copied := a
  copied

def alreadyNamed (a b : Signal Domain (BitVec 8)) : Signal Domain (BitVec 8) :=
  let sum := a + b
  sum

def nameBoundary (a b : Signal Domain (BitVec 8)) : Signal Domain (BitVec 8) :=
  let foo :=
    let foo_helper := a + b
    foo_helper
  foo

def shadowed (a : Signal Domain (BitVec 8)) : Signal Domain (BitVec 8) :=
  let foo := a
  let foo := foo
  foo

def numericSuffixBoundary
    (a : Signal Domain (BitVec 8)) : Signal Domain (BitVec 8) :=
  let output :=
    let output_1 := a
    output_1
  output

def nestedSameName
    (a : Signal Domain (BitVec 8)) : Signal Domain (BitVec 8) :=
  let foo :=
    let foo := a
    foo
  foo

def namedMemory
    (writeAddr : Signal Domain (BitVec 4))
    (writeData : Signal Domain (BitVec 8))
    (writeEnable : Signal Domain Bool)
    (readAddr : Signal Domain (BitVec 4)) :
    Signal Domain (BitVec 8) :=
  let storage := Signal.memory writeAddr writeData writeEnable readAddr
  storage

def wideAlias
    (a : Signal Domain (BitVec 80)) : Signal Domain (BitVec 80) :=
  let copied_wide := a
  copied_wide

def unpackedArrayAlias
    (a : Signal Domain (Sparkle.Core.Vector.HWVector (BitVec 8) 4)) :
    Signal Domain (Sparkle.Core.Vector.HWVector (BitVec 8) 4) :=
  let copied_array := a
  copied_array

def inlineBoolConversions
    (flag : Signal Domain Bool)
    (bit : Signal Domain (BitVec 1)) :
    Signal Domain ((BitVec 1) × Bool) :=
  bundle2
    (Sparkle.Library.RTL.boolToBV1 flag)
    (Sparkle.Library.RTL.bv1ToBool bit)

end Tests.ProvenanceFixtures
