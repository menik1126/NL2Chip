import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Given five 1-bit signals, compute all 25 pairwise XNOR comparisons.
    out[24-i*5-j] = ~input_i ^ input_j (1 if equal, 0 if different).
    Formula: ~{5{a},5{b},5{c},5{d},5{e}} ^ {5{a,b,c,d,e}} -/
def prob043_vector5 {dom : DomainConfig}
    (a b c d e : Signal dom (BitVec 1)) : Signal dom (BitVec 25) :=
  -- left25 = {aaaaa bbbbb ccccc ddddd eeeee}  (each input replicated 5 times)
  let a5 : Signal dom (BitVec 5) := a ++ a ++ a ++ a ++ a
  let b5 : Signal dom (BitVec 5) := b ++ b ++ b ++ b ++ b
  let c5 : Signal dom (BitVec 5) := c ++ c ++ c ++ c ++ c
  let d5 : Signal dom (BitVec 5) := d ++ d ++ d ++ d ++ d
  let e5 : Signal dom (BitVec 5) := e ++ e ++ e ++ e ++ e
  let left25 : Signal dom (BitVec 25) := a5 ++ b5 ++ c5 ++ d5 ++ e5
  -- right25 = {abcde abcde abcde abcde abcde}  (pattern repeated 5 times)
  let abcde : Signal dom (BitVec 5) := a ++ b ++ c ++ d ++ e
  let right25 : Signal dom (BitVec 25) := abcde ++ abcde ++ abcde ++ abcde ++ abcde
  -- XNOR: output 1 when bits are equal
  ~~~left25 ^^^ right25

#synthesizeVerilog prob043_vector5
