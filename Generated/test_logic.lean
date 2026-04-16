import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test: not_in = ~~~inp, then AND with state bits
def test_logic {dom : DomainConfig}
    (inp : Signal dom Bool)
    (state : Signal dom (BitVec 10))
    : Signal dom (BitVec 1) :=
  -- Convert inp to BitVec 1
  let i : Signal dom (BitVec 1) := Signal.mux inp (Signal.pure 1#1) (Signal.pure 0#1)
  let ni : Signal dom (BitVec 1) := ~~~i  -- NOT
  -- Extract bit 5
  let s5 : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 5 1) state
  -- ns8 = !in && s5
  ni &&& s5

#synthesize test_logic
