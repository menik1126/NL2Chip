import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test: &&& with literal
def testAndWithLit {dom : DomainConfig}
    (q : Signal dom (BitVec 4)) : Signal dom (BitVec 4) :=
  Signal.map (fun (v : BitVec 4) =>
    v &&& 7#4) q

#synthesizeVerilog testAndWithLit
