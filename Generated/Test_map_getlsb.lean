import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_map_getlsb {dom : DomainConfig}
    (a : Signal dom (BitVec 8)) : Signal dom Bool :=
  Signal.map (fun x => x.getLsb 7) a

#synthesizeVerilog test_map_getlsb
