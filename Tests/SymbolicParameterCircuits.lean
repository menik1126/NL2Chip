import Sparkle

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def symbolicIdentity {dom : DomainConfig} {W : Nat}
    (x : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  x

def symbolicXor {dom : DomainConfig} {W : Nat}
    (lhs rhs : Signal dom (BitVec W)) : Signal dom (BitVec W) :=
  lhs ^^^ rhs
