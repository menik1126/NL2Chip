import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- K-map implementation using mux inputs -/
def prob093_ece241_2014_q3 {dom : DomainConfig}
    (c d : Signal dom (BitVec 1)) : Signal dom (BitVec 4) :=
  let mux_in0 := c ||| d
  let mux_in1 := Signal.pure 0#1
  let mux_in2 := ~~~d
  let mux_in3 := c &&& d
  -- Concatenate: mux_in[3:0] = {mux_in3, mux_in2, mux_in1, mux_in0}
  -- bundle2 creates pairs, so we need to extract in the right order
  Signal.map (fun x => 
    let b0 := x.1.1.1  -- mux_in0
    let b1 := x.1.1.2  -- mux_in1
    let b2 := x.1.2    -- mux_in2
    let b3 := x.2      -- mux_in3
    b0 ++ b1 ++ b2 ++ b3) 
    (bundle2 (bundle2 (bundle2 mux_in0 mux_in1) mux_in2) mux_in3)

#synthesizeVerilog prob093_ece241_2014_q3
