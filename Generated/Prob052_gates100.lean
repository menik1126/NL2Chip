import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 100-input AND/OR/XOR gate reduction circuit -/
def prob052_gates100 {dom : DomainConfig}
    (in_ : Signal dom (BitVec 100))
    : Signal dom (BitVec 1 × BitVec 1 × BitVec 1) :=
  -- Split 100 into 50+50
  let in_lo50 : Signal dom (BitVec 50) := Signal.map (fun x => x.extractLsb' 0 50) in_
  let in_hi50 : Signal dom (BitVec 50) := Signal.map (fun x => x.extractLsb' 50 50) in_
  let and50 := in_lo50 &&& in_hi50
  let or50  := in_lo50 ||| in_hi50
  let xor50 := in_lo50 ^^^ in_hi50
  -- Split 50 into 25+25
  let lo25_and : Signal dom (BitVec 25) := Signal.map (fun x => x.extractLsb' 0 25) and50
  let hi25_and : Signal dom (BitVec 25) := Signal.map (fun x => x.extractLsb' 25 25) and50
  let lo25_or  : Signal dom (BitVec 25) := Signal.map (fun x => x.extractLsb' 0 25) or50
  let hi25_or  : Signal dom (BitVec 25) := Signal.map (fun x => x.extractLsb' 25 25) or50
  let lo25_xor : Signal dom (BitVec 25) := Signal.map (fun x => x.extractLsb' 0 25) xor50
  let hi25_xor : Signal dom (BitVec 25) := Signal.map (fun x => x.extractLsb' 25 25) xor50
  let and25 := lo25_and &&& hi25_and
  let or25  := lo25_or  ||| hi25_or
  let xor25 := lo25_xor ^^^ hi25_xor
  -- Now reduce 25 bits. Split 25 = 12 + 12 + 1
  -- First: take lo12 = bits[0..11], mi12 = bits[12..23], hi1 = bit[24]
  let lo12_and : Signal dom (BitVec 12) := Signal.map (fun x => x.extractLsb' 0 12) and25
  let mi12_and : Signal dom (BitVec 12) := Signal.map (fun x => x.extractLsb' 12 12) and25
  let b24_and  : Signal dom (BitVec 1)  := Signal.map (fun x => x.extractLsb' 24 1)  and25
  let lo12_or  : Signal dom (BitVec 12) := Signal.map (fun x => x.extractLsb' 0 12) or25
  let mi12_or  : Signal dom (BitVec 12) := Signal.map (fun x => x.extractLsb' 12 12) or25
  let b24_or   : Signal dom (BitVec 1)  := Signal.map (fun x => x.extractLsb' 24 1)  or25
  let lo12_xor : Signal dom (BitVec 12) := Signal.map (fun x => x.extractLsb' 0 12) xor25
  let mi12_xor : Signal dom (BitVec 12) := Signal.map (fun x => x.extractLsb' 12 12) xor25
  let b24_xor  : Signal dom (BitVec 1)  := Signal.map (fun x => x.extractLsb' 24 1)  xor25
  -- Combine lo12 and mi12
  let a12_and := lo12_and &&& mi12_and
  let a12_or  := lo12_or  ||| mi12_or
  let a12_xor := lo12_xor ^^^ mi12_xor
  -- Split 12 = 6 + 6
  let lo6_and : Signal dom (BitVec 6) := Signal.map (fun x => x.extractLsb' 0 6) a12_and
  let hi6_and : Signal dom (BitVec 6) := Signal.map (fun x => x.extractLsb' 6 6) a12_and
  let lo6_or  : Signal dom (BitVec 6) := Signal.map (fun x => x.extractLsb' 0 6) a12_or
  let hi6_or  : Signal dom (BitVec 6) := Signal.map (fun x => x.extractLsb' 6 6) a12_or
  let lo6_xor : Signal dom (BitVec 6) := Signal.map (fun x => x.extractLsb' 0 6) a12_xor
  let hi6_xor : Signal dom (BitVec 6) := Signal.map (fun x => x.extractLsb' 6 6) a12_xor
  let a6_and := lo6_and &&& hi6_and
  let a6_or  := lo6_or  ||| hi6_or
  let a6_xor := lo6_xor ^^^ hi6_xor
  -- Split 6 = 3 + 3
  let lo3_and : Signal dom (BitVec 3) := Signal.map (fun x => x.extractLsb' 0 3) a6_and
  let hi3_and : Signal dom (BitVec 3) := Signal.map (fun x => x.extractLsb' 3 3) a6_and
  let lo3_or  : Signal dom (BitVec 3) := Signal.map (fun x => x.extractLsb' 0 3) a6_or
  let hi3_or  : Signal dom (BitVec 3) := Signal.map (fun x => x.extractLsb' 3 3) a6_or
  let lo3_xor : Signal dom (BitVec 3) := Signal.map (fun x => x.extractLsb' 0 3) a6_xor
  let hi3_xor : Signal dom (BitVec 3) := Signal.map (fun x => x.extractLsb' 3 3) a6_xor
  let a3_and := lo3_and &&& hi3_and
  let a3_or  := lo3_or  ||| hi3_or
  let a3_xor := lo3_xor ^^^ hi3_xor
  -- Reduce 3 bits: split 3 = 1+1+1
  let b0_and : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 0 1) a3_and
  let b1_and : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 1 1) a3_and
  let b2_and : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 2 1) a3_and
  let b0_or  : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 0 1) a3_or
  let b1_or  : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 1 1) a3_or
  let b2_or  : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 2 1) a3_or
  let b0_xor : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 0 1) a3_xor
  let b1_xor : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 1 1) a3_xor
  let b2_xor : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 2 1) a3_xor
  -- Combine 3 bits to 1 bit (a3), then AND/OR/XOR with b24 (the 25th bit)
  let r_and := b0_and &&& b1_and &&& b2_and &&& b24_and
  let r_or  := b0_or  ||| b1_or  ||| b2_or  ||| b24_or
  let r_xor := b0_xor ^^^ b1_xor ^^^ b2_xor ^^^ b24_xor
  bundle2 r_and (bundle2 r_or r_xor)

#synthesizeVerilog prob052_gates100
