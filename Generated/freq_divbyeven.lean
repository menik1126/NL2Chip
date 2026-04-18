import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Frequency divider that divides clock by even numbers (NUM_DIV=6).
    Counts cycles and toggles output every NUM_DIV/2 cycles. -/
def freq_divbyeven {dom : DomainConfig}
    (rst_n : Signal dom Bool) : Signal dom (BitVec 1) :=
  Signal.circuit do
    -- State registers: 4-bit counter and 1-bit clk_div
    let cnt ← Signal.reg 0#4;
    let clk_div ← Signal.reg 0#1;
    
    -- NUM_DIV = 6, so limit is NUM_DIV/2 - 1 = 2
    let atLimit := cnt === 2#4;
    
    -- Next counter: reset to 0 if at limit, else increment
    let nextCnt := Signal.mux atLimit 0#4 (cnt + 1#4);
    
    -- Next clk_div: toggle if at limit, else keep same
    let nextClkDiv := Signal.mux atLimit (~~~clk_div) clk_div;
    
    -- Apply reset: when rst_n is low (false), reset both to 0
    let nextCntWithReset := Signal.mux rst_n nextCnt 0#4;
    let nextClkDivWithReset := Signal.mux rst_n nextClkDiv 0#1;
    
    -- Update registers
    cnt <~ nextCntWithReset;
    clk_div <~ nextClkDivWithReset;
    
    -- Return clk_div output
    return clk_div

#synthesizeVerilog freq_divbyeven
