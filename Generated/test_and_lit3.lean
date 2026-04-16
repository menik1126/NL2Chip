import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Try: use And.and (pure BitVec and) with explicit types
def testAndLit3 {dom : DomainConfig}
    (q : Signal dom (BitVec 256)) : Signal dom (BitVec 256) :=
  Signal.map (fun (v : BitVec 256) =>
    let n0 : BitVec 4 := (BitVec.extractLsb' 0 1 v).zeroExtend 4
    let masked : BitVec 4 := BitVec.and n0 7#4
    masked.zeroExtend 256) q

#synthesizeVerilog testAndLit3
