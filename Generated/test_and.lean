import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Helper to extract bit i from a 100-bit vector -/
def getBit (i : Nat) (x : BitVec 100) : BitVec 1 :=
  BitVec.extractLsb' i 1 x

/-- Test: just AND reduction of 4 bits -/
def test_and {dom : DomainConfig}
    (input : Signal dom (BitVec 100))
    : Signal dom (BitVec 1) :=
  let b0 := Signal.map (getBit 0) input
  let b1 := Signal.map (getBit 1) input
  let b2 := Signal.map (getBit 2) input
  let b3 := Signal.map (getBit 3) input
  (((b0 &&& b1) &&& b2) &&& b3)

#synthesizeVerilog test_and
