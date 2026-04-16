import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test {dom : DomainConfig}
    (state : Signal dom (BitVec 10))
    : Signal dom Bool :=
  Signal.map (fun (x : BitVec 10) => x.getLsb 6) state

#synthesizeVerilog test
