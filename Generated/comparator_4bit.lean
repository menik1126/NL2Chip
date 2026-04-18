import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 4-bit comparator: compares two 4-bit numbers A and B, returns (A_greater, A_equal, A_less) -/
def comparator_4bit {dom : DomainConfig}
    (A B : Signal dom (BitVec 4))
    : Signal dom (BitVec 1 × BitVec 1 × BitVec 1) :=
  -- Extend to 5 bits to capture carry-out (borrow)
  let A_ext := Signal.map (fun a => BitVec.zeroExtend 5 a) A
  let B_ext := Signal.map (fun b => BitVec.zeroExtend 5 b) B
  
  -- Perform subtraction A - B (5-bit result)
  let result := A_ext - B_ext
  
  -- Extract carry-out (bit 4) and difference (bits 3:0)
  let cout := Signal.map (fun r => BitVec.extractLsb 4 4 r) result
  let diff := Signal.map (fun r => BitVec.extractLsb 3 0 r) result
  
  -- A_less = cout (borrow occurred)
  let A_less := cout
  
  -- A_equal = (A == B)
  let A_equal_bool := A === B
  let A_equal := Signal.mux A_equal_bool (Signal.pure 1#1) (Signal.pure 0#1)
  
  -- A_greater = ~cout && (diff != 0)
  let not_cout := ~~~cout
  let diff_is_zero := diff === Signal.pure 0#4
  let diff_nonzero := Signal.mux diff_is_zero (Signal.pure 0#1) (Signal.pure 1#1)
  let A_greater := not_cout &&& diff_nonzero
  
  bundleAll! [A_greater, A_equal, A_less]

#synthesizeVerilog comparator_4bit
