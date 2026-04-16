import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- One-hot FSM next-state logic: computes Y1 (next B) and Y3 (next D) from state y and input w.
    Y1 = y[0] & ~w  (state A with input 0 → next state B)
    Y3 = (y[1]|y[2]|y[4]|y[5]) & w  (states B,C,E,F with input 1 → next state D) -/
def prob099_m2014_q6c {dom : DomainConfig}
    (y : Signal dom (BitVec 6)) (w : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1) :=
  -- Y1 = y[0] & ~w
  let y0  : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 0 1) y
  let y1  : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 1 1) y
  let y2  : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 2 1) y
  let y4  : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 4 1) y
  let y5  : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 5 1) y
  let not_w : Signal dom (BitVec 1) := ~~~w
  let Y1 : Signal dom (BitVec 1) := y0 &&& not_w
  let Y3 : Signal dom (BitVec 1) := (y1 ||| y2 ||| y4 ||| y5) &&& w
  bundle2 Y1 Y3

#synthesizeVerilog prob099_m2014_q6c
