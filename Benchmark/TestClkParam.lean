import Sparkle
import Sparkle.Compiler.Elab
open Sparkle.Core.Domain
open Sparkle.Core.Signal
open Sparkle.Library.RTL
def test_clk_param {dom : DomainConfig}
    (clk : Signal dom Bool)
    (d   : Signal dom (BitVec 8))
    : Signal dom (BitVec 8) :=
  Signal.mux clk d (Signal.pure 0#8)
#synthesizeVerilog test_clk_param
