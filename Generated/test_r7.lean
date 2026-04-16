import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_r7 {dom : DomainConfig}
    (r : Signal dom (BitVec 3))
    : Signal dom Bool :=
  let r0 : Signal dom Bool := Signal.map (fun x => x.getLsb 0) r
  r0

#synthesizeVerilog test_r7
