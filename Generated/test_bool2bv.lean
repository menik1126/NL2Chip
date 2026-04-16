import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test: can we use Bool arithmetic to build BitVec?
-- Bool is treated as 1-bit in hardware
-- What if we extend Bool to BitVec 1 using zeroExtend?
def test2 {dom : DomainConfig}
    (inp : Signal dom (BitVec 4)) : Signal dom (BitVec 4) :=
  Signal.map (fun (v : BitVec 4) =>
    -- Test: compute a boolean and use it in bit operations
    let b : Bool := v.getLsbD 0
    let bv : BitVec 1 := if b then 1#1 else 0#1
    BitVec.append (v.extractLsb' 1 3) bv
  ) inp

#synthesizeVerilog test2
