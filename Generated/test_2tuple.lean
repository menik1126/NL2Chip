import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_2tuple {dom : DomainConfig}
    (a b : Signal dom (BitVec 5))
    : Signal dom (BitVec 8 × BitVec 8) :=
  let w := Signal.map (fun x => x ++ 0#3) a
  let x := Signal.map (fun x => x ++ 0#3) b
  bundle2 w x

#synthesizeVerilog test_2tuple
