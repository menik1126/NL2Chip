import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test sign extend -/
def test_signextend {dom : DomainConfig}
    (input : Signal dom (BitVec 8)) : Signal dom (BitVec 32) :=
  Signal.map (fun x => x.signExtend 32) input

#synthesizeVerilog test_signextend
