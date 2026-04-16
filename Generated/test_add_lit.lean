import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test: + with literal inside let binding in Signal.map
def testAddLit {dom : DomainConfig}
    (q : Signal dom (BitVec 256)) : Signal dom (BitVec 4) :=
  Signal.map (fun (v : BitVec 256) =>
    let x : BitVec 4 := (BitVec.extractLsb' 0 1 v).zeroExtend 4
    x + 1#4) q

#synthesizeVerilog testAddLit
