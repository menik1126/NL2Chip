import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 16-bit unsigned multiplier using shift-and-accumulate algorithm -/
def multi_16bit {dom : DomainConfig}
    (start : Signal dom Bool)
    (ain : Signal dom (BitVec 16))
    (bin : Signal dom (BitVec 16))
    : Signal dom (BitVec 32 × BitVec 1) :=
  let state := Signal.loop fun state =>
    let i := projN! state 5 0
    let areg := projN! state 5 1
    let breg := projN! state 5 2
    let yout_r := projN! state 5 3
    let done_r := projN! state 5 4
    
    -- Counter logic
    let i_eq_17 := i === 17#5
    let i_lt_17 := Signal.map (fun b => !b) i_eq_17
    let start_and_lt17 := start &&& i_lt_17
    let i_next := Signal.mux start 
      (Signal.mux start_and_lt17 (i + 1#5) i)
      (Signal.pure 0#5)
    
    -- Done flag logic
    let i_eq_16 := i === 16#5
    let done_next := Signal.mux i_eq_16 (Signal.pure 1#1)
      (Signal.mux i_eq_17 (Signal.pure 0#1) done_r)
    
    -- Load and accumulation logic
    let i_eq_0 := i === 0#5
    let start_and_i0 := start &&& i_eq_0
    
    -- When start and i==0, load inputs and reset yout
    let areg_next := Signal.mux start_and_i0 ain areg
    let breg_next := Signal.mux start_and_i0 bin breg
    let yout_reset := Signal.mux start_and_i0 (Signal.pure 0#32) yout_r
    
    -- When start and 0 < i < 17, accumulate
    let i_gt_0 := Signal.map (fun b => !b) i_eq_0
    let i_gt_0_and_lt_17 := i_gt_0 &&& i_lt_17
    let should_check := start &&& i_gt_0_and_lt_17
    
    let bit_index := i - 1#5
    let areg_idx_pair := bundle2 areg bit_index
    let areg_lsb : Signal dom (BitVec 16) := Signal.map (fun (p : BitVec 16 × BitVec 5) => 
      (p.1 >>> p.2.toNat) &&& 1) areg_idx_pair
    let areg_bit : Signal dom Bool := areg_lsb === (Signal.pure 1)
    
    let breg_ext : Signal dom (BitVec 32) := Signal.map (fun b => b.zeroExtend 32) breg
    let breg_idx_pair := bundle2 breg_ext bit_index
    let breg_sh : Signal dom (BitVec 32) := Signal.map (fun (p : BitVec 32 × BitVec 5) => 
      p.1 <<< p.2.toNat) breg_idx_pair
    
    let should_acc := should_check &&& areg_bit
    let yout_next := Signal.mux should_acc (yout_reset + breg_sh) yout_reset
    
    bundleAll! [
      Signal.register 0#5 i_next,
      Signal.register 0#16 areg_next,
      Signal.register 0#16 breg_next,
      Signal.register 0#32 yout_next,
      Signal.register 0#1 done_next
    ]
  
  bundleAll! [projN! state 5 3, projN! state 5 4]

#synthesizeVerilog multi_16bit
