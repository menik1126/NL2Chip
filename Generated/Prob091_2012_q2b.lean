import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- FSM one-hot next-state logic: Y1 = y[0]&w, Y3 = (y[1]|y[2]|y[4]|y[5])&~w -/
def prob091_2012_q2b {dom : DomainConfig}
    (y : Signal dom (BitVec 6)) (w : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1) :=
  let y0 := Signal.map (fun v => v.extractLsb' 0 1) y
  let y1 := Signal.map (fun v => v.extractLsb' 1 1) y
  let y2 := Signal.map (fun v => v.extractLsb' 2 1) y
  let y4 := Signal.map (fun v => v.extractLsb' 4 1) y
  let y5 := Signal.map (fun v => v.extractLsb' 5 1) y
  let notw := ~~~w
  let Y1 := y0 &&& w
  let Y3 := (y1 ||| y2 ||| y4 ||| y5) &&& notw
  bundle2 Y1 Y3

#synthesizeVerilog prob091_2012_q2b
