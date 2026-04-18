import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Data width conversion: 8-bit to 16-bit with valid signaling.
    Accumulates two 8-bit inputs and outputs them as one 16-bit word. -/
def width_8to16 {dom : DomainConfig}
    (rst_n : Signal dom Bool)
    (valid_in : Signal dom Bool)
    (data_in : Signal dom (BitVec 8))
    : Signal dom (BitVec 1 × BitVec 16) :=
  -- Combined state: flag (1 bit) ++ data_lock (8 bits) = 9 bits total
  let state := Signal.loop fun (s : Signal dom (BitVec 9)) =>
    -- Extract flag (bit 8) and data_lock (bits 7:0)
    let flag := s.map (fun x => x.extractLsb 8 8)
    let data_lock := s.map (fun x => x.extractLsb 7 0)
    
    -- Update data_lock: capture data_in when valid_in && !flag
    let flag_is_zero := flag === Signal.pure 0#1
    let capture := valid_in &&& flag_is_zero
    let next_data_lock := Signal.mux capture data_in data_lock
    
    -- Update flag: toggle on valid_in
    let flag_flipped := ~~~flag
    let next_flag := Signal.mux valid_in flag_flipped flag
    
    -- Apply reset (rst_n is active-low, so !rst_n means reset)
    let not_rst_n := ~~~rst_n
    let next_flag_reset := Signal.mux not_rst_n (Signal.pure 0#1) next_flag
    let next_data_lock_reset := Signal.mux not_rst_n (Signal.pure 0#8) next_data_lock
    
    -- Combine into 9-bit state: {flag, data_lock}
    let next_state := (fun f dl => (f ++ dl : BitVec 9)) <$> next_flag_reset <*> next_data_lock_reset
    Signal.register 0#9 next_state
  
  -- Extract state components
  let flag := state.map (fun x => x.extractLsb 8 8)
  let data_lock := state.map (fun x => x.extractLsb 7 0)
  
  -- Generate outputs
  let flag_is_one := flag === Signal.pure 1#1
  let output_valid_bool := valid_in &&& flag_is_one
  let output_valid := Signal.mux output_valid_bool (Signal.pure 1#1) (Signal.pure 0#1)
  
  let data_out_val := (fun dl di => (dl ++ di : BitVec 16)) <$> data_lock <*> data_in
  let data_out_final := Signal.mux output_valid_bool data_out_val (Signal.pure 0#16)
  
  bundle2 output_valid data_out_final

#synthesizeVerilog width_8to16
