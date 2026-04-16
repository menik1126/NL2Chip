import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Test bit to position -/
def test_bit_pos {dom : DomainConfig}
    (a : Signal dom (BitVec 1))
    : Signal dom (BitVec 10) :=
  Signal.map (fun b => (b.toNat <<< 3 : BitVec 10)) a

#synthesizeVerilog test_bit_pos
