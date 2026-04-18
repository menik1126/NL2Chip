import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 4-bit comparator: compares two 4-bit numbers A and B, returns (A_greater, A_equal, A_less) -/
def comparator_4bit {dom : DomainConfig}
    (A B : Signal dom (BitVec 4))
    : Signal dom (BitVec 1 × BitVec 1 × BitVec 1) :=
  -- Check if A == B
  let eq_bool := A === B
  let A_equal := Signal.mux eq_bool (Signal.pure 1#1) (Signal.pure 0#1)
  
  -- Compute difference A - B
  let diff := A - B
  
  -- Check if diff is zero
  let diff_is_zero := diff === Signal.pure 0#4
  
  -- To detect borrow in A - B:
  -- Zero-extend A and B to 5 bits, subtract, and check bit 4 (borrow bit)
  let A_5bit := Signal.map (fun x => x.zeroExtend 5) A
  let B_5bit := Signal.map (fun x => x.zeroExtend 5) B
  let diff_5bit := A_5bit - B_5bit
  
  -- Extract bit 4 (MSB) as borrow indicator
  -- If borrow = 1, then A < B
  let borrow := Signal.map (fun d => d.extractLsb 4 4) diff_5bit
  let A_less := borrow
  
  -- A > B if no borrow and diff != 0
  let no_borrow_bool := borrow === Signal.pure 0#1
  let A_greater := Signal.mux (no_borrow_bool &&& (~~~diff_is_zero)) (Signal.pure 1#1) (Signal.pure 0#1)
  
  bundle2 A_greater (bundle2 A_equal A_less)

#synthesizeVerilog comparator_4bit
