import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 8-bit 2's complement adder with signed overflow detection.
    Computes s = a + b and overflow = true when signed overflow occurs
    (inputs have same sign but result has different sign).
    
    Overflow condition: !(a[7]^b[7]) && (a[7] != s[7])
    Equivalently: (~(a7 XOR b7)) AND (a7 XOR s7)
    Using BitVec 1 arithmetic: (a7 XNOR b7) AND (a7 XOR s7)
    where XNOR = ~XOR = 1 iff same, XOR = 1 iff different
-/
def prob033_ece241_2014_q1c {dom : DomainConfig}
    (a b : Signal dom (BitVec 8))
    : Signal dom (BitVec 8 × BitVec 1) :=
  -- Compute 8-bit sum
  let s := a + b
  -- Extract sign bits (bit 7) as BitVec 1 signals using extractLsb'
  let a7 : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 7 1) a
  let b7 : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 7 1) b
  let s7 : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 7 1) s
  -- same_sign = ~(a7 XOR b7): XNOR = 1 when a and b have same sign
  let same_sign : Signal dom (BitVec 1) := ~~~(a7 ^^^ b7)
  -- diff_sign = a7 XOR s7: 1 when result sign differs from input sign  
  let diff_sign : Signal dom (BitVec 1) := a7 ^^^ s7
  -- overflow = same_sign AND diff_sign
  let overflow : Signal dom (BitVec 1) := same_sign &&& diff_sign
  bundle2 s overflow

#synthesizeVerilog prob033_ece241_2014_q1c
