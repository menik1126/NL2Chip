import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test if Signal.mux with Bool input from Signal.map getLsb works
def test_fsm {dom : DomainConfig}
    (w : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  Signal.mux w (Signal.pure 1#1) (Signal.pure 0#1)

#synthesize test_fsm
