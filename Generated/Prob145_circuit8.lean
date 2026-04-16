import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Sequential circuit: p is a transparent latch (p=a when clock=1, holds when clock=0);
    q is a negedge-triggered D flip-flop (q captures a on falling edge of clock). -/
def prob145_circuit8 {dom : DomainConfig}
    (a : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1) :=
  -- q: negedge-triggered D flip-flop — captures a on falling edge of clock
  let q := Signal.registerNeg 0#1 a
  -- p: transparent latch — a when clock=1, holds last negedge value when clock=0
  -- The held value is a captured at negedge (same register as q)
  let p := Signal.mux Signal.clock a q
  bundle2 p q

#synthesizeVerilog prob145_circuit8
