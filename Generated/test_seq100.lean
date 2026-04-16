import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def test_seq {dom : DomainConfig}
    (input : Signal dom (BitVec 100)) : Signal dom (BitVec 100) :=
  let lsb_only := input &&& (1#100 : BitVec 100)
  let lsb_at_top := lsb_only <<< 99#100
  let rotated := (input >>> 1#100) ||| lsb_at_top
  input ^^^ rotated

#synthesizeVerilog test_seq
