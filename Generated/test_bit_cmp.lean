import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test bit extraction and comparison -/
def test_bit_cmp {dom : DomainConfig}
    (input : Signal dom (BitVec 8)) : Signal dom (BitVec 3) :=
  let bit0 := Signal.map (fun x => BitVec.extractLsb' 0 1 x) input
  let cond := bit0 === Signal.pure 1#1
  Signal.mux cond (Signal.pure 0#3) (Signal.pure 1#3)

#synthesizeVerilog test_bit_cmp
