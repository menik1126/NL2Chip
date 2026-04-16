import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Helper function to reverse a 4-bit BitVec using mask and shift operations -/
def reverseBits4 (bv : BitVec 4) : BitVec 4 :=
  let one : BitVec 4 := 1#4
  let b0 := ((bv >>> 0) &&& one) <<< 3
  let b1 := ((bv >>> 1) &&& one) <<< 2
  let b2 := ((bv >>> 2) &&& one) <<< 1
  let b3 := ((bv >>> 3) &&& one) <<< 0
  b0 ||| b1 ||| b2 ||| b3

/-- Reverse 4-bit vector -/
def test_reverse4 {dom : DomainConfig}
    (input : Signal dom (BitVec 4)) : Signal dom (BitVec 4) :=
  Signal.map reverseBits4 input

#synthesizeVerilog test_reverse4
