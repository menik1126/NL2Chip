import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Helper function to reverse a 4-bit BitVec using only append -/
def reverseBits4 (bv : BitVec 4) : BitVec 4 :=
  -- Build {bv[0], bv[1], bv[2], bv[3]}
  let b0 := BitVec.ofBool (bv.getLsb 0)
  let b1 := BitVec.ofBool (bv.getLsb 1)
  let b2 := BitVec.ofBool (bv.getLsb 2)
  let b3 := BitVec.ofBool (bv.getLsb 3)
  -- Concatenate: MSB first
  let r01 := BitVec.append b0 b1  -- 2 bits
  let r012 := BitVec.append r01 b2  -- 3 bits
  BitVec.append r012 b3  -- 4 bits

/-- Reverse 4-bit vector -/
def test_reverse4 {dom : DomainConfig}
    (input : Signal dom (BitVec 4)) : Signal dom (BitVec 4) :=
  Signal.map reverseBits4 input

#synthesizeVerilog test_reverse4
