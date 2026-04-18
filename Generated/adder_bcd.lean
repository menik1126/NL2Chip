import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- BCD adder: adds two 4-bit BCD digits (0-9) with carry-in, produces BCD sum and carry-out. -/
def adder_bcd {dom : DomainConfig}
    (a b : Signal dom (BitVec 4)) (cin : Signal dom Bool)
    : Signal dom (BitVec 4 × BitVec 1) :=
  -- Zero-extend inputs to 5 bits for addition
  let a5 := Signal.map (fun x => x.zeroExtend 5) a
  let b5 := Signal.map (fun x => x.zeroExtend 5) b
  let cin5 := Signal.mux cin (Signal.pure 1#5) (Signal.pure 0#5)
  -- Perform 5-bit addition: A + B + Cin (result is 0-19)
  let temp_sum := a5 + b5 + cin5
  -- Check if sum > 9 (i.e., >= 10)
  -- We check all values from 10 to 19
  let ge10 := temp_sum === 10#5
  let ge11 := temp_sum === 11#5
  let ge12 := temp_sum === 12#5
  let ge13 := temp_sum === 13#5
  let ge14 := temp_sum === 14#5
  let ge15 := temp_sum === 15#5
  let ge16 := temp_sum === 16#5
  let ge17 := temp_sum === 17#5
  let ge18 := temp_sum === 18#5
  let ge19 := temp_sum === 19#5
  let needs_correction := ge10 ||| ge11 ||| ge12 ||| ge13 ||| ge14 ||| ge15 ||| ge16 ||| ge17 ||| ge18 ||| ge19
  -- If sum >= 10, add 6 for BCD correction
  let corrected_sum := Signal.mux needs_correction (temp_sum + 6#5) temp_sum
  -- Extract lower 4 bits for the BCD sum output
  let sum := Signal.map (fun x => x.truncate 4) corrected_sum
  -- Carry out is set when correction was needed
  let cout := Signal.mux needs_correction (Signal.pure 1#1) (Signal.pure 0#1)
  bundle2 sum cout

#synthesizeVerilog adder_bcd
