import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test extractLsb -/
def test_extractlsb {dom : DomainConfig}
    (input : Signal dom (BitVec 8)) : Signal dom (BitVec 1) :=
  Signal.map (fun x => BitVec.extractLsb' 0 1 x) input

#synthesizeVerilog test_extractlsb
