import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test getLsbD -/
def test_getlsbd {dom : DomainConfig}
    (input : Signal dom (BitVec 8)) : Signal dom Bool :=
  Signal.map (fun x => x.getLsbD 0) input

#synthesizeVerilog test_getlsbd
