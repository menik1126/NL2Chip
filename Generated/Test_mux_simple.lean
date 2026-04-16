import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_mux_simple {dom : DomainConfig}
    (cond : Signal dom Bool) (a : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  Signal.mux cond (Signal.pure 0xFF#8) (Signal.pure 0#8)

#synthesizeVerilog test_mux_simple
