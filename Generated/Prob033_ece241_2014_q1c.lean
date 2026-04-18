import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 8-bit signed adder with overflow detection -/
def prob033_ece241_2014_q1c {dom : DomainConfig}
    (a b : Signal dom (BitVec 8))
    : Signal dom (BitVec 8 × BitVec 1) :=
  let s := a + b
  -- Extract sign bits (MSB): use BitVec.extractLsb to get bit 7
  let a_sign := Signal.map (fun x => BitVec.extractLsb 7 7 x) a
  let b_sign := Signal.map (fun x => BitVec.extractLsb 7 7 x) b
  let s_sign := Signal.map (fun x => BitVec.extractLsb 7 7 x) s
  -- Overflow = !(a[7]^b[7]) && (a[7] != s[7])
  -- = (a[7] == b[7]) && (a[7] != s[7])
  let same_sign := ~~~(a_sign ^^^ b_sign)
  let diff_result := a_sign ^^^ s_sign
  let overflow := same_sign &&& diff_result
  bundle2 s overflow

#synthesizeVerilog prob033_ece241_2014_q1c
