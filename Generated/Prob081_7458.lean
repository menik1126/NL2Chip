import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 7458 chip: four AND gates and two OR gates. p1y = (p1a&p1b&p1c)|(p1d&p1e&p1f), p2y = (p2a&p2b)|(p2c&p2d). -/
def prob081_7458 {dom : DomainConfig}
    (p1a p1b p1c p1d p1e p1f : Signal dom (BitVec 1))
    (p2a p2b p2c p2d : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1) :=
  let p1y := (p1a &&& p1b &&& p1c) ||| (p1d &&& p1e &&& p1f)
  let p2y := (p2a &&& p2b) ||| (p2c &&& p2d)
  bundle2 p1y p2y

#synthesizeVerilog prob081_7458
