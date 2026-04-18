import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Reverse the bit ordering of an 8-bit input. -/
def prob006_vectorr {dom : DomainConfig}
    (input : Signal dom (BitVec 8)) : Signal dom (BitVec 8) :=
  let b0 := (input &&& (1#8 : BitVec 8)) <<< (7#8 : BitVec 8)
  let b1 := (input &&& (2#8 : BitVec 8)) <<< (5#8 : BitVec 8)
  let b2 := (input &&& (4#8 : BitVec 8)) <<< (3#8 : BitVec 8)
  let b3 := (input &&& (8#8 : BitVec 8)) <<< (1#8 : BitVec 8)
  let b4 := (input &&& (16#8 : BitVec 8)) >>> (1#8 : BitVec 8)
  let b5 := (input &&& (32#8 : BitVec 8)) >>> (3#8 : BitVec 8)
  let b6 := (input &&& (64#8 : BitVec 8)) >>> (5#8 : BitVec 8)
  let b7 := (input &&& (128#8 : BitVec 8)) >>> (7#8 : BitVec 8)
  b0 ||| b1 ||| b2 ||| b3 ||| b4 ||| b5 ||| b6 ||| b7

#synthesizeVerilog prob006_vectorr
