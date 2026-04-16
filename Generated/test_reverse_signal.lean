import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Reverse 4-bit vector directly at Signal level -/
def test_reverse4 {dom : DomainConfig}
    (input : Signal dom (BitVec 4)) : Signal dom (BitVec 4) :=
  let one := Signal.pure (1#4 : BitVec 4)
  let b0 := (((input >>> one) &&& one) <<< (3#4 : BitVec 4))
  let b1 := ((((input >>> one) >>> one) &&& one) <<< (2#4 : BitVec 4))
  let b2 := (((((input >>> one) >>> one) >>> one) &&& one) <<< (1#4 : BitVec 4))
  let b3 := ((((((input >>> one) >>> one) >>> one) >>> one) &&& one)
  b0 ||| b1 ||| b2 ||| b3

#synthesizeVerilog test_reverse4
