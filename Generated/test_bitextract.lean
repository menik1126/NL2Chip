import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test {dom : DomainConfig}
    (state : Signal dom (BitVec 10))
    : Signal dom Bool :=
  let bit6 := (state >>> 6#10) &&& Signal.pure 1#10
  Signal.map (fun x => x != 0#10) bit6

#synthesizeVerilog test
