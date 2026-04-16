import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Concatenate six 5-bit inputs with 2'b11, split into four 8-bit outputs.
    assign {w,x,y,z} = {a,b,c,d,e,f,2'b11} -/
def prob064_vector3 {dom : DomainConfig}
    (a b c d e f : Signal dom (BitVec 5))
    : Signal dom (BitVec 8 × BitVec 8 × BitVec 8 × BitVec 8) :=
  -- Build full 32-bit concatenation: {a,b,c,d,e,f,2'b11}
  -- a at [31:27], b at [26:22], c at [21:17], d at [16:12],
  -- e at [11:7], f at [6:2], 2'b11 at [1:0]
  let a32 : Signal dom (BitVec 32) := Signal.map (fun v => v.zeroExtend 32) a
  let b32 : Signal dom (BitVec 32) := Signal.map (fun v => v.zeroExtend 32) b
  let c32 : Signal dom (BitVec 32) := Signal.map (fun v => v.zeroExtend 32) c
  let d32 : Signal dom (BitVec 32) := Signal.map (fun v => v.zeroExtend 32) d
  let e32 : Signal dom (BitVec 32) := Signal.map (fun v => v.zeroExtend 32) e
  let f32 : Signal dom (BitVec 32) := Signal.map (fun v => v.zeroExtend 32) f
  let const3 : Signal dom (BitVec 32) := Signal.pure 3#32
  let full : Signal dom (BitVec 32) :=
    (a32 <<< 27#32) |||
    (b32 <<< 22#32) |||
    (c32 <<< 17#32) |||
    (d32 <<< 12#32) |||
    (e32 <<<  7#32) |||
    (f32 <<<  2#32) |||
    const3
  -- Extract the four 8-bit output slices
  let w : Signal dom (BitVec 8) := Signal.map (fun v => BitVec.extractLsb' 24 8 v) full
  let x : Signal dom (BitVec 8) := Signal.map (fun v => BitVec.extractLsb' 16 8 v) full
  let y : Signal dom (BitVec 8) := Signal.map (fun v => BitVec.extractLsb'  8 8 v) full
  let z : Signal dom (BitVec 8) := Signal.map (fun v => BitVec.extractLsb'  0 8 v) full
  bundle2 w (bundle2 x (bundle2 y z))

#synthesizeVerilog prob064_vector3
