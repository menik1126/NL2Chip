import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Try: define the literal outside the lambda
private def mask4 : BitVec 4 := 7#4

def testAndLit2 {dom : DomainConfig}
    (q : Signal dom (BitVec 256)) : Signal dom (BitVec 256) :=
  Signal.map (fun (v : BitVec 256) =>
    let n0 : BitVec 4 := (BitVec.extractLsb' 0 1 v).zeroExtend 4
    let masked : BitVec 4 := n0 &&& mask4
    masked.zeroExtend 256) q

#synthesizeVerilog testAndLit2
