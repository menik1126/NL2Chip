import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test: literal in let binding, then use in operation
def testLitLet {dom : DomainConfig}
    (q : Signal dom (BitVec 256)) : Signal dom (BitVec 4) :=
  Signal.map (fun (v : BitVec 256) =>
    let mask : BitVec 4 := 7#4  -- Literal in a let binding
    let x : BitVec 4 := (BitVec.extractLsb' 0 1 v).zeroExtend 4
    x + mask) q  -- Use the mask fvar

#synthesizeVerilog testLitLet
