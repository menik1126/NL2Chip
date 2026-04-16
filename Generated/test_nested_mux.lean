import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_nested_mux {dom : DomainConfig}
    (a b c : Signal dom Bool)
    : Signal dom (BitVec 10) :=
  Signal.mux a (Signal.pure 0x001#10)
    (Signal.mux b (Signal.pure 0x002#10)
    (Signal.mux c (Signal.pure 0x004#10) (Signal.pure 0#10)))

#synthesizeVerilog test_nested_mux
