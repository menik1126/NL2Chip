import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 64-bit subtractor with overflow detection -/
def sub_64bit {dom : DomainConfig}
    (A B : Signal dom (BitVec 64))
    : Signal dom (BitVec 64 × BitVec 1) :=
  let result := A - B
  -- Extract sign bits (bit 63) by shifting right and masking to 1 bit
  let signA := (A >>> 63#64) &&& 1#64
  let signB := (B >>> 63#64) &&& 1#64
  let signResult := (result >>> 63#64) &&& 1#64
  -- Overflow: (A[63] != B[63]) && (result[63] != A[63])
  let signsAB_diff := signA ^^^ signB  -- 1 if different
  let signsAR_diff := signA ^^^ signResult  -- 1 if different
  let overflow64 := signsAB_diff &&& signsAR_diff
  -- Truncate to 1 bit
  let overflow := Signal.map (fun x => x.truncate 1) overflow64
  bundle2 result overflow

#synthesizeVerilog sub_64bit
