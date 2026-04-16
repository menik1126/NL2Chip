import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def extractBit {dom : DomainConfig} (bv : Signal dom (BitVec 255)) (idx : Nat) (h : idx < 255) : Signal dom (BitVec 8) :=
  let bit : Signal dom Bool := Signal.map (fun x => x.getLsb ⟨idx, h⟩) bv
  Signal.mux bit (Signal.pure 1#8) (Signal.pure 0#8)

def testSmall {dom : DomainConfig} (input : Signal dom (BitVec 255)) : Signal dom (BitVec 8) :=
  let b0 := extractBit input 0 (by decide)
  let b1 := extractBit input 1 (by decide)
  let b2 := extractBit input 2 (by decide)
  let b3 := extractBit input 3 (by decide)
  b0 + b1 + b2 + b3

#synthesizeVerilog testSmall
