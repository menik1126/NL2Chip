import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Karnaugh map function: f = x[2] & ~x[0] | x[3] & x[1] & x[0]
    (don't-cares used to find minimal sum-of-products expression) -/
def prob116_m2014_q3 {dom : DomainConfig}
    (x : Signal dom (BitVec 4)) : Signal dom (BitVec 1) :=
  Signal.map (fun v =>
    -- Extract individual bits as 1-bit BitVecs
    let x0 : BitVec 1 := BitVec.extractLsb' 0 1 v  -- x[0]
    let x1 : BitVec 1 := BitVec.extractLsb' 1 1 v  -- x[1]
    let x2 : BitVec 1 := BitVec.extractLsb' 2 1 v  -- x[2]
    let x3 : BitVec 1 := BitVec.extractLsb' 3 1 v  -- x[3]
    -- f = x[2] & ~x[0] | x[3] & x[1] & x[0]
    let not_x0 : BitVec 1 := ~~~x0
    let term1 : BitVec 1 := x2 &&& not_x0
    let term2 : BitVec 1 := x3 &&& x1 &&& x0
    term1 ||| term2
  ) x

#synthesizeVerilog prob116_m2014_q3
