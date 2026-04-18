import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Concatenate six 5-bit inputs and two '1' bits, then split into four 8-bit outputs -/
def prob064_vector3 {dom : DomainConfig}
    (a b c d e f : Signal dom (BitVec 5))
    : Signal dom (BitVec 8 × BitVec 8 × BitVec 8 × BitVec 8) :=
  -- Create a 32-bit concatenation by converting each 5-bit input to 32-bit and shifting
  let a32 : Signal dom (BitVec 32) := Signal.map (fun x => BitVec.shiftLeft (x.zeroExtend 32) 27) a
  let b32 : Signal dom (BitVec 32) := Signal.map (fun x => BitVec.shiftLeft (x.zeroExtend 32) 22) b
  let c32 : Signal dom (BitVec 32) := Signal.map (fun x => BitVec.shiftLeft (x.zeroExtend 32) 17) c
  let d32 : Signal dom (BitVec 32) := Signal.map (fun x => BitVec.shiftLeft (x.zeroExtend 32) 12) d
  let e32 : Signal dom (BitVec 32) := Signal.map (fun x => BitVec.shiftLeft (x.zeroExtend 32) 7) e
  let f32 : Signal dom (BitVec 32) := Signal.map (fun x => BitVec.shiftLeft (x.zeroExtend 32) 2) f
  let ones : Signal dom (BitVec 32) := Signal.pure (3#32 : BitVec 32)
  
  -- OR them all together
  let temp1 : Signal dom (BitVec 32) := a32 ||| b32
  let temp2 : Signal dom (BitVec 32) := temp1 ||| c32
  let temp3 : Signal dom (BitVec 32) := temp2 ||| d32
  let temp4 : Signal dom (BitVec 32) := temp3 ||| e32
  let temp5 : Signal dom (BitVec 32) := temp4 ||| f32
  let combined : Signal dom (BitVec 32) := temp5 ||| ones
  
  -- Split into 4 8-bit outputs
  let w := Signal.map (fun val => BitVec.extractLsb 31 24 val) combined
  let x := Signal.map (fun val => BitVec.extractLsb 23 16 val) combined
  let y := Signal.map (fun val => BitVec.extractLsb 15 8 val) combined
  let z := Signal.map (fun val => BitVec.extractLsb 7 0 val) combined
  
  -- Bundle into a 4-tuple
  bundle2 w (bundle2 x (bundle2 y z))

#synthesizeVerilog prob064_vector3
