import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Frequency divider by 3.5x using double-edge clocking technique -/
def freq_divbyfrac {dom : DomainConfig}
    (rst_n : Signal dom Bool) : Signal dom (BitVec 1) :=
  -- Counter: counts 0 to 6
  let cnt := Signal.loop fun q =>
    let at_max := q === 6#4
    let next := Signal.mux at_max (Signal.pure 0#4) (q + 1#4)
    let reset_val := Signal.mux (~~~rst_n) (Signal.pure 0#4) next
    Signal.register 0#4 reset_val
  
  -- clk_ave_r: posedge clocked, high at cnt==0 or cnt==4
  let clk_ave_r := 
    let set_at_0 := cnt === 0#4
    let set_at_4 := cnt === 4#4
    let next := Signal.mux set_at_0 (Signal.pure 1#1)
              (Signal.mux set_at_4 (Signal.pure 1#1) (Signal.pure 0#1))
    let reset_val := Signal.mux (~~~rst_n) (Signal.pure 0#1) next
    Signal.register 0#1 reset_val
  
  -- Delayed counter for negedge behavior (one cycle behind)
  let cnt_delayed := Signal.register 0#4 cnt
  
  -- clk_adjust_r: negedge clocked (modeled with delayed counter), high at cnt==1 or cnt==4
  let clk_adjust_r :=
    let set_at_1 := cnt_delayed === 1#4
    let set_at_4 := cnt_delayed === 4#4
    let next := Signal.mux set_at_1 (Signal.pure 1#1)
              (Signal.mux set_at_4 (Signal.pure 1#1) (Signal.pure 0#1))
    let reset_val := Signal.mux (~~~rst_n) (Signal.pure 0#1) next
    Signal.register 0#1 reset_val
  
  -- Output: OR of the two clocks
  clk_ave_r ||| clk_adjust_r

#synthesizeVerilog freq_divbyfrac
