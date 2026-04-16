import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test: &&& with literal inside let binding in Signal.map
def testAndLet {dom : DomainConfig}
    (q : Signal dom (BitVec 4)) : Signal dom (BitVec 4) :=
  Signal.map (fun (v : BitVec 4) =>
    let x : BitVec 4 := v &&& 7#4
    x) q

#synthesizeVerilog testAndLet
