import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_simple {dom : DomainConfig}
    (a : Signal dom Bool)
    (b : Signal dom (BitVec 10))
    : Signal dom (BitVec 1) :=
  Signal.map (fun inputs =>
    let bit0 := inputs.snd.getLsb 0
    if bit0 then 1#1 else 0#1
  ) (bundle2 a b)

#synthesizeVerilog test_simple