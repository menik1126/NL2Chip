import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_4tuple5 {dom : DomainConfig}
    (a b : Signal dom (BitVec 5))
    : Signal dom (BitVec 8 × BitVec 8 × BitVec 8 × BitVec 8) :=
  let w := Signal.map (fun x => x ++ 0#3) a
  let x := Signal.map (fun x => x ++ 0#3) b
  let y := Signal.pure 0#8
  let z := Signal.pure 0#8
  bundle2 w (bundle2 x (bundle2 y z))

#synthesizeVerilog test_4tuple5
