import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Truth table implementation: combinational logic for 3-input truth table. -/
def prob069_truthtable1 {dom : DomainConfig}
    (x3 x2 x1 : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  let term1 := (~~~x3) &&& x2 &&& (~~~x1)  -- ~x3 & x2 & ~x1
  let term2 := (~~~x3) &&& x2 &&& x1       -- ~x3 & x2 & x1
  let term3 := x3 &&& (~~~x2) &&& x1       -- x3 & ~x2 & x1
  let term4 := x3 &&& x2 &&& x1            -- x3 & x2 & x1
  term1 ||| term2 ||| term3 ||| term4

#synthesizeVerilog prob069_truthtable1
