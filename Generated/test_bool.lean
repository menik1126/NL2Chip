import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Can I convert Signal dom Bool to Signal dom (BitVec 1)?
def test_bool {dom : DomainConfig}
    (inp : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  Signal.mux inp (Signal.pure 1#1) (Signal.pure 0#1)

#synthesize test_bool
