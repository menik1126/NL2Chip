import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_fsm {dom : DomainConfig}
    (state : Signal dom (BitVec 10))
    : Signal dom (BitVec 1) :=
  -- Extract bit 8 as BitVec 1, compare with 1 to get Bool, use in mux
  let b8 : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 8 1) state
  let b9 : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 9 1) state
  -- out1 = b8 | b9 as single bit
  b8 ||| b9

#synthesize test_fsm
