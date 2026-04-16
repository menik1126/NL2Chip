import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test with large BitVec -/
def test_large {dom : DomainConfig}
    (input : Signal dom (BitVec 8))
    : Signal dom (BitVec 256) :=
  Signal.loop fun state =>
    let input_ext := Signal.map (fun x => x.zeroExtend 256) input
    let next := state + input_ext
    Signal.register 0#256 next

#synthesizeVerilog test_large
