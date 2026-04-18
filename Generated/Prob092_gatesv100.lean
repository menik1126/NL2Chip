import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Implements bit-neighbor operations on a 100-bit vector.
    Returns (out_both, out_any, out_different) where:
    - out_both[i]: in[i] AND in[i+1] (left neighbor), MSB is 0
    - out_any[i]: in[i] OR in[i-1] (right neighbor), LSB is 0
    - out_different[i]: in[i] XOR in[(i+1) mod 100] (wrapping) -/
def prob092_gatesv100 {dom : DomainConfig}
    (input : Signal dom (BitVec 100))
    : Signal dom (BitVec 100 × BitVec 100 × BitVec 100) :=
  -- out_both[i] = in[i] & in[i+1], with out_both[99] = 0
  -- in[99:1] at positions [98:0] means shift right by 1
  -- So (in >> 1)[i] = in[i+1], thus in[i] & (in >> 1)[i] = in[i] & in[i+1]
  -- Then prepend 0 at MSB: clear bit 99
  let shifted_for_both := input >>> 1#100
  let both_and := input &&& shifted_for_both
  let out_both := both_and &&& ~~~(1#100 <<< 99#100)
  
  -- out_any[i] = in[i] | in[i-1], with out_any[0] = 0
  -- in[99:1] at positions [99:1] means shift left by 1
  -- So (in << 1)[i] = in[i-1], thus in[i] | (in << 1)[i] = in[i] | in[i-1]
  -- Then append 0 at LSB: clear bit 0
  let shifted_for_any := input <<< 1#100
  let any_or := input ||| shifted_for_any
  let out_any := any_or &&& ~~~(1#100)
  
  -- out_different[i] = in[i] ^ in[(i+1) mod 100]
  -- { in[0], in[99:1] } is a right rotation by 1
  -- Extract bit 0, shift right, put bit 0 at top
  let bit0 := input &&& 1#100
  let shifted := input >>> 1#100
  let bit0_at_99 := bit0 <<< 99#100
  let rotated := shifted ||| bit0_at_99
  let out_different := input ^^^ rotated
  
  bundle2 out_both (bundle2 out_any out_different)

#synthesizeVerilog prob092_gatesv100
