import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test: &&& without literal on the right
def testAndNoLit {dom : DomainConfig}
    (q : Signal dom (BitVec 4)) : Signal dom (BitVec 4) :=
  Signal.map (fun (v : BitVec 4) =>
    let n0 : BitVec 4 := v
    let n1 : BitVec 4 := v
    n0 &&& n1) q

#synthesizeVerilog testAndNoLit
