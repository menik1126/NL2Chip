import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test zero extend -/
def test_zeroextend {dom : DomainConfig}
    (input : Signal dom (BitVec 8)) : Signal dom (BitVec 32) :=
  Signal.map (fun x => x.zeroExtend 32) input

#synthesizeVerilog test_zeroextend
