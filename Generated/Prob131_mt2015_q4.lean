import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Module A: z = (x^y) & x. Module B: z = ~(x^y) (XNOR).
    Top-level: OR(A1,B1) XOR AND(A2,B2) = x | ~y -/
def prob131_mt2015_q4 {dom : DomainConfig}
    (x y : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  -- A(x,y) = (x^y) & x
  let a1 := (x ^^^ y) &&& x
  -- B(x,y) = ~(x^y)
  let b1 := ~~~(x ^^^ y)
  -- OR of first pair
  let or1 := a1 ||| b1
  -- A(x,y) = (x^y) & x
  let a2 := (x ^^^ y) &&& x
  -- B(x,y) = ~(x^y)
  let b2 := ~~~(x ^^^ y)
  -- AND of second pair
  let and1 := a2 &&& b2
  -- XOR of OR and AND = z
  or1 ^^^ and1

#synthesizeVerilog prob131_mt2015_q4
