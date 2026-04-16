import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test basic shift operations
def test_shift {dom : DomainConfig} 
    (input : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  Signal.map (fun x => x >>> 1#8) input

#synthesizeVerilog test_shift