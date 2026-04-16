import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_append {dom : DomainConfig}
    (a b : Signal dom (BitVec 5))
    : Signal dom (BitVec 10) :=
  Signal.map (fun (a, b) => BitVec.append a b) (bundle2 a b)

#synthesizeVerilog test_append
