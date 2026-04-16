import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Direct lambda with AND constant
def test_lambda_and {dom : DomainConfig}
    (q : Signal dom (BitVec 4)) : Signal dom (BitVec 4) :=
  Signal.map (fun v => v &&& (7 : BitVec 4)) q

#synthesizeVerilog test_lambda_and
