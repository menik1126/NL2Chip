import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- FSM state output logic for y[1] and y[3] flip-flops -/
def prob091_2012_q2b {dom : DomainConfig}
    (y : Signal dom (BitVec 6)) (w : Signal dom Bool)
    : Signal dom (BitVec 1 × BitVec 1) :=
  -- Extract individual state bits
  let y0 := Signal.map (fun v => v.extractLsb 0 0) y
  let y1 := Signal.map (fun v => v.extractLsb 1 1) y
  let y2 := Signal.map (fun v => v.extractLsb 2 2) y
  let y4 := Signal.map (fun v => v.extractLsb 4 4) y
  let y5 := Signal.map (fun v => v.extractLsb 5 5) y
  
  -- Convert w to BitVec 1 for bitwise operations
  let w_bv := Signal.mux w (Signal.pure 1#1) (Signal.pure 0#1)
  let not_w := Signal.mux w (Signal.pure 0#1) (Signal.pure 1#1)
  
  -- Y1 = y[0] & w
  let Y1 := y0 &&& w_bv
  
  -- Y3 = (y[1] | y[2] | y[4] | y[5]) & ~w
  let temp := y1 ||| y2
  let temp2 := y4 ||| y5
  let or_result := temp ||| temp2
  let Y3 := or_result &&& not_w
  
  bundle2 Y1 Y3

#synthesizeVerilog prob091_2012_q2b
