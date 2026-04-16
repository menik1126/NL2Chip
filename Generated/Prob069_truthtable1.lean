import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Truth table combinational circuit: f = (~x3 & x2) | (x1 & (x3 ^ x2)) -/
def prob069_truthtable1 {dom : DomainConfig}
    (x3 x2 x1 : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  let notX3 := ~~~x3
  let notX2 := ~~~x2
  let term1 := notX3 &&& x2                -- ~x3 & x2 (covers rows 010 and 011)
  let term2 := x3 &&& notX2 &&& x1         -- x3 & ~x2 & x1 (row 101)
  let term3 := x3 &&& x2 &&& x1            -- x3 & x2 & x1 (row 111)
  term1 ||| term2 ||| term3

#synthesizeVerilog prob069_truthtable1
