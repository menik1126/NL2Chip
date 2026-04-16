import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Can Signal dom (BitVec 1) be used directly where Signal dom Bool is needed?
def test_mux {dom : DomainConfig}
    (state : Signal dom (BitVec 10))
    : Signal dom (BitVec 1) :=
  let b8 : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 8 1) state
  let b9 : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 9 1) state
  let cond : Signal dom (BitVec 1) := b8 ||| b9
  -- Can I use cond (BitVec 1) as mux condition?
  -- Need to convert to Bool somehow...
  -- Option: compare with 1
  let cond_bool : Signal dom Bool := cond === (1#1 : BitVec 1)
  Signal.mux cond_bool (Signal.pure 1#1) (Signal.pure 0#1)

#synthesize test_mux
