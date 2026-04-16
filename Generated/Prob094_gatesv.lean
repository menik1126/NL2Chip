import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Gate vector operations: computes out_both (AND with left neighbor),
    out_any (OR with right neighbor), and out_different (XOR with left
    neighbor, wrapping) for a 4-bit input vector. -/
def prob094_gatesv {dom : DomainConfig}
    (in_ : Signal dom (BitVec 4))
    : Signal dom (BitVec 4 × BitVec 4 × BitVec 4) :=
  -- Shift right by 1: in[3:1] at bits [2:0], bit 3 = 0
  let in_shr1 : Signal dom (BitVec 4) := Signal.map (fun (v : BitVec 4) =>
    BitVec.ushiftRight v 1) in_
  -- out_both[2:0] = in[2:0] & in[3:1]; out_both[3] = don't care (0)
  let out_both : Signal dom (BitVec 4) := in_ &&& in_shr1
  -- out_any[3:1] = in[3:1] | in[2:0]; out_any[0] = don't care (0)
  let out_any : Signal dom (BitVec 4) := in_ ||| in_shr1
  -- out_different = in ^ {in[0], in[3:1]}
  -- Compute rotated version: in[0] goes to bit 3, in[3:1] goes to bits [2:0]
  let in_rot1 : Signal dom (BitVec 4) := Signal.map (fun (v : BitVec 4) =>
    let shr1 : BitVec 4 := BitVec.ushiftRight v 1
    let lsb0 : BitVec 4 := (BitVec.extractLsb' 0 1 v).zeroExtend 4
    let lsb_top : BitVec 4 := BitVec.shiftLeft lsb0 3
    shr1 ||| lsb_top) in_
  let out_different : Signal dom (BitVec 4) := in_ ^^^ in_rot1
  bundle2 out_both (bundle2 out_any out_different)

#synthesizeVerilog prob094_gatesv
