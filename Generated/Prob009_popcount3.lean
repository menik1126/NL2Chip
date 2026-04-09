import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Original spec (preserved, do not modify) -/
def prob009_popcount3_spec {dom : DomainConfig}
    (input : Signal dom (BitVec 3)) : Signal dom (BitVec 2) :=
  -- Extract individual bits and add them (like reference Verilog: in[0]+in[1]+in[2])
  let bit0 := input &&& 1#3  -- Extract bit 0
  let bit1 := (input >>> 1#3) &&& 1#3  -- Extract bit 1  
  let bit2 := (input >>> 2#3) &&& 1#3  -- Extract bit 2
  -- Add the bits, then convert to BitVec 2 for the output
  Signal.map (fun x => x.truncate 2) (bit0 + bit1 + bit2)

/-- Architecture variant: Reduced intermediate signals for better area -/
def prob009_popcount3 {dom : DomainConfig}
    (input : Signal dom (BitVec 3)) : Signal dom (BitVec 2) :=
  -- Combine bit extraction and addition in one expression to reduce signals
  Signal.map (fun x => x.truncate 2) 
    ((input &&& 1#3) + ((input >>> 1#3) &&& 1#3) + ((input >>> 2#3) &&& 1#3))

/-- Equivalence proof: optimized architecture = original spec -/
theorem prob009_popcount3_equiv {dom : DomainConfig} (input : Signal dom (BitVec 3)) :
    prob009_popcount3 input = prob009_popcount3_spec input := by
  unfold prob009_popcount3 prob009_popcount3_spec
  rfl

#synthesizeVerilog prob009_popcount3