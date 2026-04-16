import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test truncate -/
def test_trunc {dom : DomainConfig}
    (input : Signal dom (BitVec 8)) : Signal dom (BitVec 3) :=
  Signal.map (fun x => x.truncate 3) input

#synthesizeVerilog test_trunc
