import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Frequency divider by odd number (default 5).
    Divides input clock frequency by NUM_DIV using two offset counters. -/
def freq_divbyodd {dom : DomainConfig}
    (rst_n : Signal dom Bool) : Signal dom (BitVec 1) :=
  -- NUM_DIV = 5
  -- Use two counters offset by NUM_DIV/2 to simulate dual-edge behavior
  
  -- Combined state: cnt1 (3 bits) | clk_div1 (1 bit) | cnt2 (3 bits) | clk_div2 (1 bit) = 8 bits
  let state := Signal.loop fun (s : Signal dom (BitVec 8)) =>
    -- Extract components
    let cnt1 := Signal.map (fun x : BitVec 8 => x.extractLsb 7 5) s
    let clk_div1 := Signal.map (fun x : BitVec 8 => x.extractLsb 4 4) s
    let cnt2 := Signal.map (fun x : BitVec 8 => x.extractLsb 3 1) s
    let clk_div2 := Signal.map (fun x : BitVec 8 => x.extractLsb 0 0) s
    
    -- Counter 1: counts 0-4
    let cnt1_at_max := cnt1 === 4#3
    let cnt1_next := Signal.mux cnt1_at_max (Signal.pure 0#3) (cnt1 + 1#3)
    
    -- clk_div1: high when cnt1 < 2
    let clk_div1_next := Signal.mux ((cnt1_next === 0#3) ||| (cnt1_next === 1#3)) 
      (Signal.pure 1#1) (Signal.pure 0#1)
    
    -- Counter 2: counts 0-4
    let cnt2_at_max := cnt2 === 4#3
    let cnt2_next := Signal.mux cnt2_at_max (Signal.pure 0#3) (cnt2 + 1#3)
    
    -- clk_div2: high when cnt2 < 2
    let clk_div2_next := Signal.mux ((cnt2_next === 0#3) ||| (cnt2_next === 1#3))
      (Signal.pure 1#1) (Signal.pure 0#1)
    
    -- Pack state back
    let cnt1_ext := Signal.map (fun x : BitVec 3 => x.zeroExtend 8) cnt1_next
    let clk_div1_ext := Signal.map (fun x : BitVec 1 => x.zeroExtend 8) clk_div1_next
    let cnt2_ext := Signal.map (fun x : BitVec 3 => x.zeroExtend 8) cnt2_next
    let clk_div2_ext := Signal.map (fun x : BitVec 1 => x.zeroExtend 8) clk_div2_next
    
    let next_s := (cnt1_ext <<< 5#8) ||| (clk_div1_ext <<< 4#8) ||| (cnt2_ext <<< 1#8) ||| clk_div2_ext
    
    -- Reset: active low, initialize to 0b00010001 (both clk_div bits high)
    let s_with_reset := Signal.mux rst_n next_s (Signal.pure 0b00010001#8)
    
    Signal.register 0b00010001#8 s_with_reset
  
  -- Extract and OR the two clk_div signals
  let clk_div1 := Signal.map (fun x : BitVec 8 => x.extractLsb 4 4) state
  let clk_div2 := Signal.map (fun x : BitVec 8 => x.extractLsb 0 0) state
  clk_div1 ||| clk_div2

#synthesizeVerilog freq_divbyodd
