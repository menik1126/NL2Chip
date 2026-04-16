import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

private abbrev st0 : BitVec 3 := 0#3
private abbrev st1 : BitVec 3 := 1#3

def test_with_bitwise {dom : DomainConfig}
    (s : Signal dom (BitVec 3))
    : Signal dom (BitVec 3) :=
  Signal.loop fun (state : Signal dom (BitVec 3)) =>
    let is0 := state === Signal.pure st0
    -- Extract bit 0 using bitwise AND
    let s0_bv := s &&& Signal.pure 1#3  -- Mask with 0b001
    let s0_is_set := s0_bv === Signal.pure 1#3
    let next := Signal.mux is0
      (Signal.mux s0_is_set (Signal.pure st1) (Signal.pure st0))
      (Signal.pure st0)
    Signal.register st0 next

#synthesizeVerilog test_with_bitwise
