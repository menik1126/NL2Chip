import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- FSM next-state logic for states Y1 and Y3 using one-hot encoding -/
def prob099_m2014_q6c {dom : DomainConfig}
    (y : Signal dom (BitVec 6)) (w : Signal dom Bool)
    : Signal dom (BitVec 1 × BitVec 1) :=
  -- Extract individual state bits
  let y0 := Signal.map (fun v => v.extractLsb 0 0) y
  let y1 := Signal.map (fun v => v.extractLsb 1 1) y
  let y2 := Signal.map (fun v => v.extractLsb 2 2) y
  let y4 := Signal.map (fun v => v.extractLsb 4 4) y
  let y5 := Signal.map (fun v => v.extractLsb 5 5) y
  
  -- Y1 = y[0] & ~w
  let notW := Signal.mux w (Signal.pure 0#1) (Signal.pure 1#1)
  let Y1 := y0 &&& notW
  
  -- Y3 = (y[1] | y[2] | y[4] | y[5]) & w
  let orTerm := y1 ||| y2 ||| y4 ||| y5
  let wBit := Signal.mux w (Signal.pure 1#1) (Signal.pure 0#1)
  let Y3 := orTerm &&& wBit
  
  bundle2 Y1 Y3

#synthesizeVerilog prob099_m2014_q6c
