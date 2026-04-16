import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_fsm {dom : DomainConfig}
    (state : Signal dom (BitVec 10))
    : Signal dom (BitVec 1) :=
  -- Extract bit 8 using extractLsb'
  Signal.map (fun x => x.extractLsb' 8 1) state

#synthesize test_fsm
