import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_4tuple {dom : DomainConfig}
    (a b : Signal dom (BitVec 5))
    : Signal dom (BitVec 8 × BitVec 8 × BitVec 8 × BitVec 8) :=
  let w := Signal.pure (0#8 : BitVec 8)
  let x := Signal.pure (0#8 : BitVec 8)
  let y := Signal.pure (0#8 : BitVec 8)
  let z := Signal.pure (0#8 : BitVec 8)
  Signal.map (fun (w, x, y, z) => (w, x, y, z)) (bundle2 (bundle2 w x) (bundle2 y z))

#synthesizeVerilog test_4tuple
