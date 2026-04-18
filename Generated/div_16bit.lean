import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 16-bit divider: divides 16-bit dividend A by 8-bit divisor B.
    Returns (quotient, remainder) as 16-bit values. -/
def div_16bit {dom : DomainConfig}
    (A : Signal dom (BitVec 16))
    (B : Signal dom (BitVec 8))
    : Signal dom (BitVec 16 × BitVec 16) :=
  -- Extend B to 32 bits and shift left by 16
  let B_ext := Signal.map (fun b => (b.zeroExtend 32).shiftLeft 16) B
  -- Extend A to 32 bits
  let A_ext := Signal.map (fun a => a.zeroExtend 32) A
  
  -- Helper to perform one division step
  let divStep (tmp_a : Signal dom (BitVec 32)) (tmp_b : Signal dom (BitVec 32)) : Signal dom (BitVec 32) :=
    let shifted := tmp_a <<< 1#32
    -- Compare: shifted >= tmp_b  (i.e., !(shifted < tmp_b))
    -- Use ule: a >= b  <==>  b <= a  <==>  b.ule(a)
    let cond := Signal.map (fun (pair : BitVec 32 × BitVec 32) => 
      let shifted_val := pair.1
      let tmp_b_val := pair.2
      tmp_b_val.ule shifted_val  -- tmp_b <= shifted, i.e., shifted >= tmp_b
    ) (bundle2 shifted tmp_b)
    let sub_result := (shifted - tmp_b) + (Signal.pure (1 : BitVec 32) : Signal dom (BitVec 32))
    Signal.mux cond sub_result shifted
  
  -- Perform 16 iterations
  let t1 := divStep A_ext B_ext
  let t2 := divStep t1 B_ext
  let t3 := divStep t2 B_ext
  let t4 := divStep t3 B_ext
  let t5 := divStep t4 B_ext
  let t6 := divStep t5 B_ext
  let t7 := divStep t6 B_ext
  let t8 := divStep t7 B_ext
  let t9 := divStep t8 B_ext
  let t10 := divStep t9 B_ext
  let t11 := divStep t10 B_ext
  let t12 := divStep t11 B_ext
  let t13 := divStep t12 B_ext
  let t14 := divStep t13 B_ext
  let t15 := divStep t14 B_ext
  let t16 := divStep t15 B_ext
  
  -- Extract result and remainder
  let result := Signal.map (fun x => x.extractLsb 15 0) t16
  let odd := Signal.map (fun x => x.extractLsb 31 16) t16
  
  bundle2 result odd

#synthesizeVerilog div_16bit
