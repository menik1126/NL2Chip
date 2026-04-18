import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Radix-2 divider for 8-bit unsigned integers (simplified version) -/
def radix2_div {dom : DomainConfig}
    (sign : Signal dom Bool)
    (dividend : Signal dom (BitVec 8))
    (divisor : Signal dom (BitVec 8))
    (opn_valid : Signal dom Bool)
    (res_ready : Signal dom Bool)
    : Signal dom (BitVec 1 × BitVec 16) :=
  Signal.circuit do
    -- State registers
    let remainder ← Signal.reg 0#8;
    let quotient ← Signal.reg 0#8;
    let cnt ← Signal.reg 0#4;
    let start_cnt ← Signal.reg 0#1;
    let res_valid ← Signal.reg 0#1;
    
    -- Control signals
    let start_cnt_bool := start_cnt === 1#1;
    let res_valid_bool := res_valid === 1#1;
    let not_start := start_cnt === 0#1;
    let not_res_valid := res_valid === 0#1;
    let should_start := not_start &&& opn_valid &&& not_res_valid;
    let cnt_done := cnt === 8#4;
    
    -- Next state logic
    let next_remainder := Signal.mux should_start
      (Signal.pure 0#8)
      (Signal.mux start_cnt_bool
        (remainder + 1#8)
        remainder);
    
    let next_quotient := Signal.mux should_start
      dividend
      (Signal.mux start_cnt_bool
        (quotient + 1#8)
        quotient);
    
    let next_cnt := Signal.mux should_start
      (Signal.pure 1#4)
      (Signal.mux start_cnt_bool
        (Signal.mux cnt_done (Signal.pure 0#4) (cnt + 1#4))
        cnt);
    
    let next_start_cnt := Signal.mux should_start
      (Signal.pure 1#1)
      (Signal.mux (start_cnt_bool &&& cnt_done)
        (Signal.pure 0#1)
        start_cnt);
    
    let data_go := res_valid_bool &&& res_ready;
    let next_res_valid := Signal.mux cnt_done
      (Signal.pure 1#1)
      (Signal.mux data_go (Signal.pure 0#1) res_valid);
    
    -- Update registers
    remainder <~ next_remainder;
    quotient <~ next_quotient;
    cnt <~ next_cnt;
    start_cnt <~ next_start_cnt;
    res_valid <~ next_res_valid;
    
    -- Output: remainder in upper 8 bits, quotient in lower 8 bits
    let result := remainder ++ quotient;
    
    return bundle2 res_valid result

#synthesizeVerilog radix2_div
