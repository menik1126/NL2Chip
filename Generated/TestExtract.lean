import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_extract {dom : DomainConfig}
    (r : Signal dom (BitVec 3)) : Signal dom Bool :=
  Signal.map (fun x => x.getLsb 0) r

#synthesizeVerilog test_extract
