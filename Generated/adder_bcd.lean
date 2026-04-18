import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- BCD adder: adds two 4-bit BCD digits (0-9) with carry-in, produces BCD sum and carry-out. -/
def adder_bcd {dom : DomainConfig}
    (a b : Signal dom (BitVec 4)) (cin : Signal dom (BitVec 1))
    : Signal dom (BitVec 4 × BitVec 1) :=
  -- Extend cin to 4 bits for addition
  let cin_ext := Signal.map (fun c => c.zeroExtend 4) cin
  -- Perform binary addition: A + B + Cin
  let temp_sum : Signal dom (BitVec 4) := a + b + cin_ext
  -- Check if sum > 9 (using ult: 9 < temp_sum means temp_sum > 9)
  let needs_correction := Signal.ult (Signal.pure 9#4) temp_sum
  -- Compute corrected sum (add 6 if needed)
  let sum_plus_6 : Signal dom (BitVec 4) := temp_sum + 6#4
  let corrected_sum := Signal.mux needs_correction sum_plus_6 temp_sum
  -- Generate carry-out
  let cout := Signal.mux needs_correction (Signal.pure 1#1) (Signal.pure 0#1)
  bundle2 corrected_sum cout

#synthesizeVerilog adder_bcd
