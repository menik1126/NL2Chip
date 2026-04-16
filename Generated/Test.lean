import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_simple {dom : DomainConfig}
    (a : Signal dom (BitVec 8))
    (b : Signal dom Bool)
    : Signal dom (BitVec 1 × BitVec 8) :=
  let out1 := Signal.mux b (Signal.pure 1#1) (Signal.pure 0#1)
  let out2 := a
  bundle2 out1 out2

#synthesizeVerilog test_simple
