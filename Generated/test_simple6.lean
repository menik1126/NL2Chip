import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_fsm {dom : DomainConfig}
    (state : Signal dom (BitVec 6))
    : Signal dom Bool :=
  Signal.map (fun x => x.getLsb 4) state

#synthesize test_fsm
