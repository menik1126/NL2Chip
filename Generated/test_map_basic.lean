import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Simple test: map that just passes through
def testMapBasic {dom : DomainConfig}
    (q : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  Signal.map (fun (v : BitVec 8) => v) q

#synthesizeVerilog testMapBasic
