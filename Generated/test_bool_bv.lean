import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Test: Bool as result of Signal.map and then using in bitwise ops
def test_bool_bv {dom : DomainConfig}
    (q : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  Signal.map (fun (v : BitVec 8) =>
    let bit0 : BitVec 1 := BitVec.extractLsb' 0 1 v
    let sum : BitVec 4 := bit0.zeroExtend 4
    let result : Bool := sum == (3 : BitVec 4)
    -- Now use result as a BitVec 1 somehow
    let bv_result : BitVec 8 := (BitVec.ofBool result).zeroExtend 8
    bv_result) q

#synthesizeVerilog test_bool_bv
