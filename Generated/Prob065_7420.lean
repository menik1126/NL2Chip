import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 7420 chip: two 4-input NAND gates -/
def prob065_7420 {dom : DomainConfig}
    (p1a p1b p1c p1d p2a p2b p2c p2d : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1) :=
  let p1y := ~~~(p1a &&& p1b &&& p1c &&& p1d)
  let p2y := ~~~(p2a &&& p2b &&& p2c &&& p2d)
  bundle2 p1y p2y

#synthesizeVerilog prob065_7420
