import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Original: two-register implementation -/
def prob045_edgedetect2_spec {dom : DomainConfig}
    (input : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  let d_last := Signal.register 0#8 input
  let edges := input ^^^ d_last
  Signal.register 0#8 edges

/-- Optimized: single-register implementation -/
def prob045_edgedetect2 {dom : DomainConfig}
    (input : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  let d_last := Signal.register 0#8 input
  input ^^^ d_last

-- Check if they're equal
#check @prob045_edgedetect2_spec
#check @prob045_edgedetect2

-- Try to see the difference
example {dom : DomainConfig} (input : Signal dom (BitVec 8)) :
    prob045_edgedetect2 input = prob045_edgedetect2_spec input := by
  unfold prob045_edgedetect2 prob045_edgedetect2_spec
  -- At this point we can see they're different
  sorry
