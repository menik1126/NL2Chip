import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 100-bit gate operations: out_both (AND with left neighbor), out_any (OR with right neighbor),
    out_different (XOR with left neighbor, wrapping). -/
def prob092_gatesv100 {dom : DomainConfig}
    (inp : Signal dom (BitVec 100))
    : Signal dom (BitVec 100 × BitVec 100 × BitVec 100) :=
  -- out_both[i] = in[i] & in[i+1] (left neighbor); out_both[99] = 0
  -- Reference: out_both = { 1'b0, in[98:0] & in[99:1] }
  -- in[99:1] at positions [98:0] = inp >>> 1  (bit 99 becomes 0)
  -- out_both = inp & (inp >>> 1)
  -- bit 99 of (inp >>> 1) = 0, so out_both[99] = inp[99] & 0 = 0 ✓
  let out_both : Signal dom (BitVec 100) :=
    inp &&& (inp >>> 1#100)
  -- out_any[i] = in[i] | in[i-1] (right neighbor); out_any[0] = 0
  -- Reference: out_any = { in[98:0] | in[99:1], 1'b0 }
  -- out_any[j] = in[j] | in[j-1] for j>=1, out_any[0] = 0
  -- (inp <<< 1)[j] = in[j-1] with (inp <<< 1)[0] = 0
  -- So out_any = (inp | (inp <<< 1)) with bit 0 masked to 0
  -- (inp <<< 1)[0] = 0 but inp[0] contributes: out_any[0] = inp[0] | 0 = inp[0] ≠ 0
  -- Must mask bit 0 to 0: AND with NOT(1)
  let one_signal : Signal dom (BitVec 100) := Signal.pure (1#100)
  let mask_no_lsb : Signal dom (BitVec 100) := ~~~one_signal
  let out_any : Signal dom (BitVec 100) :=
    (inp ||| (inp <<< 1#100)) &&& mask_no_lsb
  -- out_different[i] = in[i] ^ in[i+1] (left neighbor), wrapping
  -- Reference: out_different = in ^ { in[0], in[99:1] }
  -- { in[0], in[99:1] } is right rotation by 1: (inp >>> 1) | ((inp & 1) << 99)
  let lsb_at_top : Signal dom (BitVec 100) :=
    (inp &&& (1#100 : BitVec 100)) <<< (99#100 : BitVec 100)
  let rotated_right : Signal dom (BitVec 100) :=
    (inp >>> 1#100) ||| lsb_at_top
  let out_different : Signal dom (BitVec 100) :=
    inp ^^^ rotated_right
  bundle2 out_both (bundle2 out_any out_different)

#synthesizeVerilog prob092_gatesv100
