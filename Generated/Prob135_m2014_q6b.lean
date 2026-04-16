import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Next-state logic for y[1] (Y1) of a 6-state FSM (A-F encoded as 000-101).
    Given current state y[2:0] and input w, compute the next value of y[1].
    This is purely combinational next-state logic.
    State transitions (next state's bit 1 = Y1):
      B(001),w=0→C(010): Y1=1  B(001),w=1→D(011): Y1=1
      C(010),w=1→D(011): Y1=1
      E(100),w=1→D(011): Y1=1
      F(101),w=0→C(010): Y1=1  F(101),w=1→D(011): Y1=1
    All other transitions have Y1=0. -/
def prob135_m2014_q6b {dom : DomainConfig}
    (y : Signal dom (BitVec 3)) (w : Signal dom (BitVec 1))
    : Signal dom (BitVec 1) :=
  -- Extract each bit of y as a 1-bit signal
  let y2 : Signal dom (BitVec 1) := Signal.map (fun v => BitVec.extractLsb' 2 1 v) y
  let y1b : Signal dom (BitVec 1) := Signal.map (fun v => BitVec.extractLsb' 1 1 v) y
  let y0 : Signal dom (BitVec 1) := Signal.map (fun v => BitVec.extractLsb' 0 1 v) y
  -- Complements
  let ny2 : Signal dom (BitVec 1) := ~~~y2
  let ny1b : Signal dom (BitVec 1) := ~~~y1b
  let ny0 : Signal dom (BitVec 1) := ~~~y0
  -- Y1=1 terms:
  -- term1: State B (001), any w → ~y2&~y1&y0
  let t1 : Signal dom (BitVec 1) := ny2 &&& ny1b &&& y0
  -- term2: State C (010), w=1  → ~y2&y1&~y0&w
  let t2 : Signal dom (BitVec 1) := ny2 &&& y1b &&& ny0 &&& w
  -- term3: State E (100), w=1  → y2&~y1&~y0&w
  let t3 : Signal dom (BitVec 1) := y2 &&& ny1b &&& ny0 &&& w
  -- term4: State F (101), any w → y2&~y1&y0
  let t4 : Signal dom (BitVec 1) := y2 &&& ny1b &&& y0
  -- OR all terms
  t1 ||| t2 ||| t3 ||| t4

#synthesizeVerilog prob135_m2014_q6b
