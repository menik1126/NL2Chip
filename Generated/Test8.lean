import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test Signal.map with getLsb -/
def test8 {dom : DomainConfig}
    (y : Signal dom (BitVec 3))
    : Signal dom Bool :=
  Signal.map (fun s => s.getLsb 0) y

#synthesizeVerilog test8
