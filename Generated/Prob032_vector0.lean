import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Vector splitter: outputs the 3-bit input vector and splits it into three 1-bit outputs. -/
def prob032_vector0 {dom : DomainConfig}
    (vec : Signal dom (BitVec 3))
    : Signal dom (BitVec 3 × BitVec 1 × BitVec 1 × BitVec 1) :=
  let outv := vec
  let o2 := Signal.map (BitVec.extractLsb' 2 1) vec
  let o1 := Signal.map (BitVec.extractLsb' 1 1) vec
  let o0 := Signal.map (BitVec.extractLsb' 0 1) vec
  bundle2 outv (bundle2 o2 (bundle2 o1 o0))

#synthesizeVerilog prob032_vector0
