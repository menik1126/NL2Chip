import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Karnaugh map implementation for m2014_q3 -/
def prob116_m2014_q3 {dom : DomainConfig}
    (x : Signal dom (BitVec 4)) : Signal dom (BitVec 1) :=
  -- f=1 for x ∈ {4, 6, 11, 12, 14} based on the K-map
  let is_4 := x === Signal.pure 4#4
  let is_6 := x === Signal.pure 6#4
  let is_11 := x === Signal.pure 11#4
  let is_12 := x === Signal.pure 12#4
  let is_14 := x === Signal.pure 14#4
  
  let f_bool := ((is_4 ||| is_6) ||| (is_11 ||| is_12)) ||| is_14
  Signal.mux f_bool (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob116_m2014_q3
