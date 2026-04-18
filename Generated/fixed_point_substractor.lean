import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Fixed-point subtractor with configurable precision.
    Performs subtraction of two N-bit fixed-point numbers with Q fractional bits. -/
def fixed_point_substractor {dom : DomainConfig}
    (a b : Signal dom (BitVec 32)) : Signal dom (BitVec 32) :=
  -- Extract sign bits (MSB) and magnitudes (lower 31 bits)
  let a_sign_bit := a >>> 31#32
  let b_sign_bit := b >>> 31#32
  let a_mag := a &&& 0x7FFFFFFF#32
  let b_mag := b &&& 0x7FFFFFFF#32
  
  -- Check if signs are the same
  let same_sign := a_sign_bit === b_sign_bit
  
  -- Check if a is positive and b is negative (for different sign case)
  let a_is_pos := a_sign_bit === 0#32
  let b_is_neg := b_sign_bit === 1#32
  let a_pos_b_neg := a_is_pos &&& b_is_neg
  
  -- Magnitude comparison: a_mag > b_mag
  -- Use subtraction with borrow: if (a_mag - b_mag) has MSB set, then a < b
  -- But we need to be careful with 31-bit values
  -- Better approach: compute diff = a_mag - b_mag, check if it would underflow
  -- For 32-bit arithmetic: a > b iff (a - b) doesn't have sign bit set when treated as signed
  let diff_ab := a_mag - b_mag
  -- Since magnitudes are 31-bit (MSB always 0), diff_ab MSB tells us if underflow occurred
  let a_gt_b := (diff_ab >>> 31#32) === 0#32
  
  -- Case 1: Same sign - subtract magnitudes, preserve sign
  let mag_diff := a_mag - b_mag
  let res_same := Signal.mux (a_sign_bit === 1#32)
    (mag_diff ||| 0x80000000#32)  -- both negative: set sign bit
    mag_diff                       -- both positive: clear sign bit
  
  -- Case 2: Different signs - add magnitudes
  let mag_sum := a_mag + b_mag
  let sum_is_zero := mag_sum === 0#32
  
  -- When a is positive and b is negative: a - b = a + |b|
  -- Result is positive if a > b, negative if a < b, zero if equal
  let res_a_pos_b_neg := Signal.mux a_gt_b
    mag_sum  -- a > b: result is positive (no sign bit)
    (Signal.mux sum_is_zero 
      mag_sum  -- sum is zero: sign = 0
      (mag_sum ||| 0x80000000#32))  -- a < b: result is negative (set sign bit)
  
  -- When a is negative and b is positive: a - b = -(|a| + |b|)
  -- Result is negative if a < b (|a| > |b|), positive if a > b (|a| < |b|)
  let res_a_neg_b_pos := Signal.mux a_gt_b
    (Signal.mux sum_is_zero
      mag_sum  -- sum is zero: sign = 0
      (mag_sum ||| 0x80000000#32))  -- |a| > |b|: result is negative
    mag_sum  -- |a| < |b|: result is positive
  
  -- Select between different-sign cases
  let res_diff := Signal.mux a_pos_b_neg res_a_pos_b_neg res_a_neg_b_pos
  
  -- Final selection: same sign vs different sign
  Signal.mux same_sign res_same res_diff

#synthesizeVerilog fixed_point_substractor
