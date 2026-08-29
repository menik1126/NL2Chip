import Sparkle.Core.Signal

/-!
# Multi-Domain Circuit Interface

A `Signal dom (A × B)` can only describe outputs in one clock domain. `Circuit`
is the heterogeneous top-level interface for modules whose public outputs are
owned by different physical domains.
-/

namespace Sparkle.Core.Circuit

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- One named output with an existentially carried clock domain and width. -/
inductive Output where
  | bool (name : String) (domain : DomainConfig) (signal : Signal domain Bool)
  | bits (name : String) (domain : DomainConfig) (width : Nat)
      (signal : Signal domain (BitVec width))

/-- A synthesizable top-level interface with independently-owned outputs. -/
structure Circuit where
  outputs : List Output
  deriving Inhabited

namespace Circuit

def empty : Circuit := { outputs := [] }

def ofOutputs (outputs : List Output) : Circuit := { outputs }

/-- A first-class asynchronous FIFO component. Synthesis lowers this marker to
    binary/Gray pointers, audited pointer synchronizers, independent write/read
    state, and an explicit dual-domain asynchronous-read memory. `depth` must
    be a power of two of at least two. -/
opaque asyncFifo {writeDomain readDomain : DomainConfig} {dataWidth : Nat}
    (depth : Nat)
    (writeFullName readDataName readEmptyName : String)
    (writeIncrement : Signal writeDomain Bool)
    (writeData : Signal writeDomain (BitVec dataWidth))
    (readIncrement : Signal readDomain Bool) : Circuit

end Circuit

end Sparkle.Core.Circuit
