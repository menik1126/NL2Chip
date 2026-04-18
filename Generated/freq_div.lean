import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Frequency divider: divides 100MHz input clock to 50MHz, 10MHz, and 1MHz outputs.
    CLK_50 toggles every cycle (divide by 2).
    CLK_10 toggles every 5 cycles (divide by 10).
    CLK_1 toggles every 50 cycles (divide by 100).
-/
def freq_div {dom : DomainConfig}
    (RST : Signal dom Bool)
    : Signal dom (BitVec 1 × (BitVec 1 × BitVec 1)) :=
  -- CLK_50: toggle every cycle
  let clk50 := Signal.loop fun (q : Signal dom (BitVec 1)) =>
    let next := Signal.mux RST 0#1 (~~~q)
    Signal.register 0#1 next
  
  -- CLK_10: toggle every 5 cycles (counter 0-4)
  -- State: cnt (4 bits) ++ clk (1 bit) = 5 bits
  let state10 := Signal.loop fun (state : Signal dom (BitVec 5)) =>
    let cnt := state >>> 1#5
    let clk := state &&& 1#5
    let atMax := cnt === 4#5
    let nextCnt := Signal.mux RST 0#5 (Signal.mux atMax 0#5 (cnt + 1#5))
    let nextClk := Signal.mux RST 0#5 (Signal.mux atMax (~~~clk) clk)
    Signal.register 0#5 ((nextCnt <<< 1#5) ||| nextClk)
  let clk10 := Signal.map (fun s : BitVec 5 => s.extractLsb 0 0) state10
  
  -- CLK_1: toggle every 50 cycles (counter 0-49)
  -- State: cnt (7 bits) ++ clk (1 bit) = 8 bits
  let state1 := Signal.loop fun (state : Signal dom (BitVec 8)) =>
    let cnt := state >>> 1#8
    let clk := state &&& 1#8
    let atMax := cnt === 49#8
    let nextCnt := Signal.mux RST 0#8 (Signal.mux atMax 0#8 (cnt + 1#8))
    let nextClk := Signal.mux RST 0#8 (Signal.mux atMax (~~~clk) clk)
    Signal.register 0#8 ((nextCnt <<< 1#8) ||| nextClk)
  let clk1 := Signal.map (fun s : BitVec 8 => s.extractLsb 0 0) state1
  
  bundle2 clk50 (bundle2 clk10 clk1)

#synthesizeVerilog freq_div
