import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 8-bit multiplier using shift-and-add method -/
def multi_8bit {dom : DomainConfig}
    (a b : Signal dom (BitVec 8)) : Signal dom (BitVec 16) :=
  let a16 := Signal.map (fun v => v.zeroExtend 16) a
  -- Extract each bit of b and conditionally add the shifted multiplicand
  let bit0 := (b &&& (1#8 : BitVec 8)) === (1#8 : BitVec 8)
  let bit1 := (b &&& (2#8 : BitVec 8)) === (2#8 : BitVec 8)
  let bit2 := (b &&& (4#8 : BitVec 8)) === (4#8 : BitVec 8)
  let bit3 := (b &&& (8#8 : BitVec 8)) === (8#8 : BitVec 8)
  let bit4 := (b &&& (16#8 : BitVec 8)) === (16#8 : BitVec 8)
  let bit5 := (b &&& (32#8 : BitVec 8)) === (32#8 : BitVec 8)
  let bit6 := (b &&& (64#8 : BitVec 8)) === (64#8 : BitVec 8)
  let bit7 := (b &&& (128#8 : BitVec 8)) === (128#8 : BitVec 8)
  -- Create shifted versions of a16 using the Signal shift operator
  let a_shift0 := a16
  let a_shift1 := a16 <<< (1#16 : BitVec 16)
  let a_shift2 := a16 <<< (2#16 : BitVec 16)
  let a_shift3 := a16 <<< (3#16 : BitVec 16)
  let a_shift4 := a16 <<< (4#16 : BitVec 16)
  let a_shift5 := a16 <<< (5#16 : BitVec 16)
  let a_shift6 := a16 <<< (6#16 : BitVec 16)
  let a_shift7 := a16 <<< (7#16 : BitVec 16)
  -- Conditionally add each term (use a16 - a16 to get zero of the right type)
  let zero := a16 - a16
  let term0 := Signal.mux bit0 a_shift0 zero
  let term1 := Signal.mux bit1 a_shift1 zero
  let term2 := Signal.mux bit2 a_shift2 zero
  let term3 := Signal.mux bit3 a_shift3 zero
  let term4 := Signal.mux bit4 a_shift4 zero
  let term5 := Signal.mux bit5 a_shift5 zero
  let term6 := Signal.mux bit6 a_shift6 zero
  let term7 := Signal.mux bit7 a_shift7 zero
  -- Sum all terms
  term0 + term1 + term2 + term3 + term4 + term5 + term6 + term7

#synthesizeVerilog multi_8bit
