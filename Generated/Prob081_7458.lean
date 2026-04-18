import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 7458 chip: implements AND-OR logic with 10 inputs and 2 outputs.
    p1y = (p1a & p1b & p1c) | (p1d & p1e & p1f)
    p2y = (p2a & p2b) | (p2c & p2d) -/
def prob081_7458 {dom : DomainConfig}
    (p1a p1b p1c p1d p1e p1f p2a p2b p2c p2d : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1) :=
  let and1 := p1a &&& p1b &&& p1c
  let and2 := p1d &&& p1e &&& p1f
  let p1y := and1 ||| and2
  let and3 := p2a &&& p2b
  let and4 := p2c &&& p2d
  let p2y := and3 ||| and4
  bundle2 p1y p2y

#synthesizeVerilog prob081_7458
