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

namespace Circuit

def empty : Circuit := { outputs := [] }

def ofOutputs (outputs : List Output) : Circuit := { outputs }

end Circuit

end Sparkle.Core.Circuit
