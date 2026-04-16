import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test flat output -/
def test_flat {dom : DomainConfig}
    (a : Signal dom (BitVec 10))
    (b : Signal dom (BitVec 1))
    : Signal dom (BitVec 12) :=
  let c := Signal.pure 0#1
  Signal.map (fun x => x.1 ++ x.2.1 ++ x.2.2) (bundle2 a (bundle2 b c))

#synthesizeVerilog test_flat
