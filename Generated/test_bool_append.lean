import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test: can Bool be used with BitVec.append?
-- Idea: Bool to BitVec 1 via decidable equality
def test_bool_append {dom : DomainConfig}
    (q : Signal dom (BitVec 8)) : Signal dom (BitVec 9) :=
  Signal.map (fun (v : BitVec 8) =>
    let b : Bool := v == (3 : BitVec 8)
    -- Try to use Bool as BitVec 1:
    -- (b : Bool) → BitVec 1 via decide/Bool operations
    -- Bool and BitVec 1 are isomorphic in hardware
    -- BitVec.append needs BitVec args
    -- What if we AND with 1?
    let bv : BitVec 1 := (if b then (1 : BitVec 1) else (0 : BitVec 1))
    BitVec.append bv v
  ) q

#synthesizeVerilog test_bool_append
