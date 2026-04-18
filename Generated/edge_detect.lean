import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Edge detector: detects rising and falling edges on input signal a.
    Outputs rise=1 for one cycle after 0→1 transition,
    down=1 for one cycle after 1→0 transition. -/
def edge_detect {dom : DomainConfig}
    (a : Signal dom (BitVec 1)) : Signal dom (BitVec 1 × BitVec 1) :=
  -- Register the previous input value
  let a_prev := Signal.register 0#1 a
  -- Detect rising edge: a=1 AND a_prev=0
  let rise_comb := a &&& (~~~a_prev)
  -- Detect falling edge: a=0 AND a_prev=1
  let down_comb := (~~~a) &&& a_prev
  -- Register the outputs
  let rise := Signal.register 0#1 rise_comb
  let down := Signal.register 0#1 down_comb
  -- Bundle the two outputs
  bundle2 rise down

#synthesizeVerilog edge_detect
