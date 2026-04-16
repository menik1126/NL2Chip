import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test if Signal.map getLsb works as input to Signal.mux
def test_fsm {dom : DomainConfig}
    (state : Signal dom (BitVec 10))
    : Signal dom Bool :=
  Signal.map (fun x => x.getLsb 8) state

#synthesize test_fsm
