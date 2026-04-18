import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 4-input gates: AND, OR, XOR reduction on 4-bit input. -/
def prob051_gates4 {dom : DomainConfig}
    (input : Signal dom (BitVec 4))
    : Signal dom ((BitVec 1 × BitVec 1) × BitVec 1) :=
  let out_and := input &&& (input >>> 1#4) &&& (input >>> 2#4) &&& (input >>> 3#4)
  let out_or := input ||| (input >>> 1#4) ||| (input >>> 2#4) ||| (input >>> 3#4)
  let out_xor := input ^^^ (input >>> 1#4) ^^^ (input >>> 2#4) ^^^ (input >>> 3#4)
  
  let and_bit := Signal.map (fun x : BitVec 4 => x.truncate 1) out_and
  let or_bit := Signal.map (fun x : BitVec 4 => x.truncate 1) out_or
  let xor_bit := Signal.map (fun x : BitVec 4 => x.truncate 1) out_xor
  
  bundle2 (bundle2 and_bit or_bit) xor_bit

#synthesizeVerilog prob051_gates4
