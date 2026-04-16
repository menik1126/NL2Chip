import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Direct lambda with constant
def test_lambda_const {dom : DomainConfig}
    (q : Signal dom (BitVec 4)) : Signal dom (BitVec 4) :=
  Signal.map (fun v => v + (3 : BitVec 4)) q

#synthesizeVerilog test_lambda_const
