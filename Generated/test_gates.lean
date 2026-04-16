import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Helper to extract bit i from a 100-bit vector -/
def getBit (i : Nat) (x : BitVec 100) : BitVec 1 :=
  BitVec.extractLsb' i 1 x

/-- Test: AND, OR, XOR reduction of 4 bits -/
def test_gates {dom : DomainConfig}
    (input : Signal dom (BitVec 100))
    : Signal dom (BitVec 1 × BitVec 1 × BitVec 1) :=
  let b0 := Signal.map (getBit 0) input
  let b1 := Signal.map (getBit 1) input
  let b2 := Signal.map (getBit 2) input
  let b3 := Signal.map (getBit 3) input
  
  let out_and := (((b0 &&& b1) &&& b2) &&& b3)
  let out_or := (((b0 ||| b1) ||| b2) ||| b3)
  let out_xor := (((b0 ^^^ b1) ^^^ b2) ^^^ b3)
  
  bundle2 out_and (bundle2 out_or out_xor)

#synthesizeVerilog test_gates
