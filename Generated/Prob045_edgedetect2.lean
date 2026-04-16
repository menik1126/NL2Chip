import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 8-bit any-edge detector: output is high for one cycle after each bit
    transitions from 0→1 or 1→0 (XOR of current and previous input). -/
def prob045_edgedetect2 {dom : DomainConfig}
    (input : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  -- Register the previous input value
  let d_last := Signal.register 0#8 input
  -- Detect any edge: XOR of current and previous (1 when they differ)
  let edges := input ^^^ d_last
  -- Register the edge detection output (matches Verilog behavior)
  Signal.register 0#8 edges

#synthesizeVerilog prob045_edgedetect2
