import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Extract 2-bit counter from PHT at given index -/
def getPHTEntry (pht : BitVec 256) (index : BitVec 7) : BitVec 2 :=
  0#2  -- Simplified for testing

/-- Test with getPHTEntry -/
def test_pht {dom : DomainConfig}
    (index : Signal dom (BitVec 7))
    : Signal dom (BitVec 256) :=
  Signal.loop fun pht_state =>
    Signal.register 0#256 pht_state

#synthesizeVerilog test_pht
