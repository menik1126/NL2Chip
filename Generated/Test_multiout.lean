import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test multi-output with loop -/
def test_multiout {dom : DomainConfig}
    (reset : Signal dom Bool)
    : Signal dom (BitVec 1 × BitVec 4) :=
  let q := Signal.loop fun (q : Signal dom (BitVec 4)) =>
    let next := Signal.mux reset (Signal.pure 0#4) (q + 1#4)
    Signal.register 0#4 next
  let flag := Signal.mux (q === Signal.pure 10#4) (Signal.pure 1#1) (Signal.pure 0#1)
  bundle2 flag q

#synthesizeVerilog test_multiout
