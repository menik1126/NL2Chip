import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_fsm {dom : DomainConfig}
    (state : Signal dom (BitVec 10))
    : Signal dom (BitVec 1) :=
  let s8 : Signal dom Bool := Signal.map (fun x => x.getLsb 8) state
  Signal.mux s8 (Signal.pure 1#1) (Signal.pure 0#1)

#synthesize test_fsm
