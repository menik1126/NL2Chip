import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 8-bit positive edge detector: output is high for one cycle after
    each bit transitions from 0 to 1. -/
def prob054_edgedetect {dom : DomainConfig}
    (input : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  -- Register the previous input value
  let d_last := Signal.register 0#8 input
  -- Detect 0→1 transitions: current=1 AND previous=0
  let edges := input &&& (~~~d_last)
  -- Register the edge detection output (matches Verilog behavior)
  Signal.register 0#8 edges

#synthesizeVerilog prob054_edgedetect
