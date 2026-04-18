import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Bit neighbor relationships: out_both, out_any, out_different -/
def prob094_gatesv {dom : DomainConfig}
    (input : Signal dom (BitVec 4))
    : Signal dom ((BitVec 4 × BitVec 4) × BitVec 4) :=
  -- out_both[i] = in[i] & in[i+1] for i=0,1,2
  let lower3 := input &&& (0b0111#4 : BitVec 4)
  let upper3_both := (input >>> 1#4) &&& (0b0111#4 : BitVec 4)
  let out_both := lower3 &&& upper3_both
  
  -- out_any[i] = in[i] | in[i-1] for i=1,2,3
  let upper3_any := (input >>> 1#4) &&& (0b0111#4 : BitVec 4)
  let lower3_any := input &&& (0b0111#4 : BitVec 4)
  let out_any := (upper3_any ||| lower3_any) <<< 1#4
  
  -- out_different[i] = in[i] ^ in[(i+1) mod 4]
  let rotated := ((input &&& (0b0001#4 : BitVec 4)) <<< 3#4) ||| (input >>> 1#4)
  let out_different := input ^^^ rotated
  
  bundle2 (bundle2 out_both out_any) out_different

#synthesizeVerilog prob094_gatesv
