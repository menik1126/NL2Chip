import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- ECE241 2013 Q2: Logic circuit with SOP and POS outputs -/
def prob070_ece241_2013_q2 {dom : DomainConfig}
    (a b c d : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1) :=
  -- Sum of Products form: c&d | ~a&~b&c
  let out_sop := (c &&& d) ||| ((~~~a) &&& (~~~b) &&& c)
  
  -- Product of Sums form: c & (~b|d) & (~a|d)
  let out_pos := c &&& ((~~~b) ||| d) &&& ((~~~a) ||| d)
  
  bundle2 out_sop out_pos

#synthesizeVerilog prob070_ece241_2013_q2
