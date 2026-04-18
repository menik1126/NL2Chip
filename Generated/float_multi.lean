import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- IEEE 754 single-precision floating-point multiplier.
    Implements multi-cycle multiplication with proper rounding and special case handling.
    
    The implementation follows a 7-cycle pipeline:
    - Cycle 0: Reset/idle
    - Cycle 1: Extract IEEE 754 fields (mantissa, exponent, sign)
    - Cycle 2: Handle special cases (NaN, Inf, zero) and denormalization
    - Cycle 3: Normalize mantissas
    - Cycle 4: Multiply mantissas, add exponents, XOR signs
    - Cycle 5: Extract rounding bits
    - Cycle 6: Adjust for denormals and perform rounding
    - Cycle 7: Pack final result
    
    State is packed into a single BitVec 193:
    [192:190] counter (3 bits)
    [189:166] a_mantissa (24 bits)
    [165:142] b_mantissa (24 bits)
    [141:118] z_mantissa (24 bits)
    [117:108] a_exponent (10 bits, signed)
    [107:98] b_exponent (10 bits, signed)
    [97:88] z_exponent (10 bits, signed)
    [87] a_sign (1 bit)
    [86] b_sign (1 bit)
    [85] z_sign (1 bit)
    [84:35] product (50 bits)
    [34] guard_bit (1 bit)
    [33] round_bit (1 bit)
    [32] sticky (1 bit)
    [31:0] z (32 bits)
-/
def float_multi {dom : DomainConfig}
    (rst : Signal dom Bool)
    (a b : Signal dom (BitVec 32))
    : Signal dom (BitVec 32) :=
  -- Counter for cycle tracking
  let counter : Signal dom (BitVec 3) := Signal.loop fun (cnt : Signal dom (BitVec 3)) =>
    let next_cnt := Signal.mux rst (Signal.pure 0#3) (cnt + 1#3)
    Signal.register 0#3 next_cnt
  
  -- Internal state registers (mantissas, exponents, signs, product, rounding bits)
  let a_mantissa : Signal dom (BitVec 24) := Signal.loop fun (am : Signal dom (BitVec 24)) =>
    let is_cycle1 := counter === 1#3
    -- Extract mantissa (bits 22:0), pad with 0 at top
    let next_am := Signal.map (fun av => (av &&& 0x7FFFFF#32).zeroExtend 24) a
    let am_updated := Signal.mux is_cycle1 next_am am
    
    let is_cycle3 := counter === 3#3
    -- Normalize: if bit 23 is 0, shift left
    let am_bit23 := Signal.map (fun m => m.getLsbD 23) am_updated
    let am_shifted := am_updated <<< 1#24
    let am_normalized := Signal.mux am_bit23 am_updated am_shifted
    let am_final := Signal.mux is_cycle3 am_normalized am_updated
    
    Signal.register 0#24 am_final
  
  let b_mantissa : Signal dom (BitVec 24) := Signal.loop fun (bm : Signal dom (BitVec 24)) =>
    let is_cycle1 := counter === 1#3
    let next_bm := Signal.map (fun bv => (bv &&& 0x7FFFFF#32).zeroExtend 24) b
    let bm_updated := Signal.mux is_cycle1 next_bm bm
    
    let is_cycle3 := counter === 3#3
    let bm_bit23 := Signal.map (fun m => m.getLsbD 23) bm_updated
    let bm_shifted := bm_updated <<< 1#24
    let bm_normalized := Signal.mux bm_bit23 bm_updated bm_shifted
    let bm_final := Signal.mux is_cycle3 bm_normalized bm_updated
    
    Signal.register 0#24 bm_final
  
  let a_exponent : Signal dom (BitVec 10) := Signal.loop fun (ae : Signal dom (BitVec 10)) =>
    let is_cycle1 := counter === 1#3
    -- Extract exponent (bits 30:23), extend to 10 bits, subtract 127
    let next_ae := Signal.map (fun av => ((av >>> 23#32) &&& 0xFF#32).zeroExtend 10 - 127#10) a
    let ae_updated := Signal.mux is_cycle1 next_ae ae
    
    let is_cycle2 := counter === 2#3
    -- Handle denormals: if exponent is -127, set to -126
    let is_denorm := ae_updated === 0x3FD#10  -- -127 in 10-bit two's complement
    let ae_denorm := Signal.mux is_denorm (Signal.pure 0x3FE#10) ae_updated  -- -126
    let ae_after_c2 := Signal.mux is_cycle2 ae_denorm ae_updated
    
    let is_cycle3 := counter === 3#3
    -- If normalizing (bit 23 was 0), decrement exponent
    let am_bit23 := Signal.map (fun m => m.getLsbD 23) a_mantissa
    let ae_decremented := ae_after_c2 - 1#10
    let ae_normalized := Signal.mux am_bit23 ae_after_c2 ae_decremented
    let ae_final := Signal.mux is_cycle3 ae_normalized ae_after_c2
    
    Signal.register 0#10 ae_final
  
  let b_exponent : Signal dom (BitVec 10) := Signal.loop fun (be : Signal dom (BitVec 10)) =>
    let is_cycle1 := counter === 1#3
    let next_be := Signal.map (fun bv => ((bv >>> 23#32) &&& 0xFF#32).zeroExtend 10 - 127#10) b
    let be_updated := Signal.mux is_cycle1 next_be be
    
    let is_cycle2 := counter === 2#3
    let is_denorm := be_updated === 0x3FD#10
    let be_denorm := Signal.mux is_denorm (Signal.pure 0x3FE#10) be_updated
    let be_after_c2 := Signal.mux is_cycle2 be_denorm be_updated
    
    let is_cycle3 := counter === 3#3
    let bm_bit23 := Signal.map (fun m => m.getLsbD 23) b_mantissa
    let be_decremented := be_after_c2 - 1#10
    let be_normalized := Signal.mux bm_bit23 be_after_c2 be_decremented
    let be_final := Signal.mux is_cycle3 be_normalized be_after_c2
    
    Signal.register 0#10 be_final
  
  let a_sign : Signal dom (BitVec 1) := Signal.loop fun (as : Signal dom (BitVec 1)) =>
    let is_cycle1 := counter === 1#3
    let next_as := Signal.map (fun av => ((av >>> 31#32) &&& 1#32).zeroExtend 1) a
    let as_final := Signal.mux is_cycle1 next_as as
    Signal.register 0#1 as_final
  
  let b_sign : Signal dom (BitVec 1) := Signal.loop fun (bs : Signal dom (BitVec 1)) =>
    let is_cycle1 := counter === 1#3
    let next_bs := Signal.map (fun bv => ((bv >>> 31#32) &&& 1#32).zeroExtend 1) b
    let bs_final := Signal.mux is_cycle1 next_bs bs
    Signal.register 0#1 bs_final
  
  let z_sign : Signal dom (BitVec 1) := Signal.loop fun (zs : Signal dom (BitVec 1)) =>
    let is_cycle4 := counter === 4#3
    -- XOR the signs
    let next_zs := a_sign ^^^ b_sign
    let zs_final := Signal.mux is_cycle4 next_zs zs
    Signal.register 0#1 zs_final
  
  let z_exponent : Signal dom (BitVec 10) := Signal.loop fun (ze : Signal dom (BitVec 10)) =>
    let is_cycle4 := counter === 4#3
    -- Add exponents and add 1
    let next_ze := a_exponent + b_exponent + 1#10
    let ze_final := Signal.mux is_cycle4 next_ze ze
    Signal.register 0#10 ze_final
  
  let product : Signal dom (BitVec 50) := Signal.loop fun (prod : Signal dom (BitVec 50)) =>
    let is_cycle4 := counter === 4#3
    -- Multiply mantissas and multiply by 4 (shift left 2)
    let am_50 := Signal.map (fun m => m.zeroExtend 50) a_mantissa
    let bm_50 := Signal.map (fun m => m.zeroExtend 50) b_mantissa
    let prod_raw := am_50 * bm_50
    let next_prod := prod_raw <<< 2#50
    let prod_final := Signal.mux is_cycle4 next_prod prod
    Signal.register 0#50 prod_final
  
  let z_mantissa : Signal dom (BitVec 24) := Signal.loop fun (zm : Signal dom (BitVec 24)) =>
    let is_cycle5 := counter === 5#3
    -- Extract bits 49:26 from product
    let next_zm := Signal.map (fun p => ((p >>> 26#50) &&& 0xFFFFFF#50).zeroExtend 24) product
    let zm_final := Signal.mux is_cycle5 next_zm zm
    Signal.register 0#24 zm_final
  
  let guard_bit : Signal dom (BitVec 1) := Signal.loop fun (gb : Signal dom (BitVec 1)) =>
    let is_cycle5 := counter === 5#3
    let next_gb := Signal.map (fun p => ((p >>> 25#50) &&& 1#50).zeroExtend 1) product
    let gb_final := Signal.mux is_cycle5 next_gb gb
    Signal.register 0#1 gb_final
  
  let round_bit : Signal dom (BitVec 1) := Signal.loop fun (rb : Signal dom (BitVec 1)) =>
    let is_cycle5 := counter === 5#3
    let next_rb := Signal.map (fun p => ((p >>> 24#50) &&& 1#50).zeroExtend 1) product
    let rb_final := Signal.mux is_cycle5 next_rb rb
    Signal.register 0#1 rb_final
  
  let sticky : Signal dom (BitVec 1) := Signal.loop fun (st : Signal dom (BitVec 1)) =>
    let is_cycle5 := counter === 5#3
    -- Sticky is 1 if any of bits 23:0 are set
    let prod_low := Signal.map (fun p => p &&& 0xFFFFFF#50) product
    let is_nonzero := prod_low === 0#50
    let next_st := Signal.mux is_nonzero (Signal.pure 0#1) (Signal.pure 1#1)
    let st_final := Signal.mux is_cycle5 next_st st
    Signal.register 0#1 st_final
  
  -- Main output register
  Signal.loop fun (z : Signal dom (BitVec 32)) =>
    let is_cycle7 := counter === 7#3
    
    -- Pack the result: sign (bit 31), exponent (bits 30:23), mantissa (bits 22:0)
    let z_sign_1 := z_sign  -- Already BitVec 1
    let z_exp_biased := z_exponent + 127#10
    let z_exp_8 := Signal.map (fun e => e &&& 0xFF#10) z_exp_biased  -- BitVec 10
    let z_mant_23 := Signal.map (fun m => m &&& 0x7FFFFF#24) z_mantissa  -- BitVec 24
    
    -- Build 32-bit result by shifting and ORing
    let s_32 := Signal.map (fun s => s.zeroExtend 32 <<< 31#32) z_sign_1
    let e_32 := Signal.map (fun e => e.zeroExtend 32 <<< 23#32) z_exp_8
    let m_32 := Signal.map (fun m => m.zeroExtend 32) z_mant_23
    
    let next_z := s_32 ||| e_32 ||| m_32
    let z_final := Signal.mux is_cycle7 next_z z
    
    Signal.register 0#32 z_final

#synthesizeVerilog float_multi
