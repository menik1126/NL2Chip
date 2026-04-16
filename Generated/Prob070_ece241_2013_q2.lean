import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Combinational logic with four inputs (a,b,c,d) producing minimum SOP and POS forms.
    out_sop = c&d | ~a&~b&c
    out_pos = c & (~b|d) & (~a|b) -/
def prob070_ece241_2013_q2 {dom : DomainConfig}
    (a b c d : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1) :=
  let out_sop := (c &&& d) ||| ((~~~ a) &&& (~~~ b) &&& c)
  let out_pos := c &&& ((~~~ b) ||| d) &&& ((~~~ a) ||| b)
  bundle2 out_sop out_pos

#synthesizeVerilog prob070_ece241_2013_q2
