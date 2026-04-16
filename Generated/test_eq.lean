import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test: equality comparison 
def test_eq {dom : DomainConfig}
    (q : Signal dom (BitVec 8)) : Signal dom Bool :=
  Signal.map (fun (v : BitVec 8) =>
    let bit0 : BitVec 1 := BitVec.extractLsb' 0 1 v
    let sum : BitVec 4 := bit0.zeroExtend 4
    sum == (3 : BitVec 4)) q

#synthesizeVerilog test_eq
