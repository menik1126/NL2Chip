import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Karnaugh map implementation with don't-care optimization -/
def prob125_kmap3 {dom : DomainConfig}
    (a b c d : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  -- Use BitVec operations directly
  let not_b := ~~~b
  let not_d := ~~~d
  let not_a := ~~~a
  
  let term1 := a &&& not_b  -- a & ~b
  let term2 := a &&& b &&& not_d  -- a & b & ~d
  let term3 := a &&& b &&& c  -- a & b & c
  let term4 := not_a &&& c &&& not_d  -- ~a & c & ~d
  
  term1 ||| term2 ||| term3 ||| term4

#synthesizeVerilog prob125_kmap3
