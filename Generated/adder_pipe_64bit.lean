import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 64-bit pipelined ripple carry adder with 4 stages (16 bits each).
    Returns bundled (result: 65-bit sum, o_en: output enable). -/
def adder_pipe_64bit {dom : DomainConfig}
    (i_en : Signal dom Bool)
    (adda addb : Signal dom (BitVec 64))
    : Signal dom (BitVec 65 × BitVec 1) :=
  -- Convert Bool to BitVec 1
  let i_en_bv := Signal.mux i_en (Signal.pure 1#1) (Signal.pure 0#1)
  
  -- Stage 1: compute lower 16 bits
  let stage1_en := Signal.register (0#1) i_en_bv
  let a1 : Signal dom (BitVec 64) := adda &&& (Signal.pure (0xFFFF#64) : Signal dom (BitVec 64))
  let b1 : Signal dom (BitVec 64) := addb &&& (Signal.pure (0xFFFF#64) : Signal dom (BitVec 64))
  let sum1 : Signal dom (BitVec 64) := a1 + b1
  let s1 := Signal.register (0#64) (sum1 &&& (Signal.pure (0xFFFF#64) : Signal dom (BitVec 64)))
  let c1 := Signal.register (0#64) (Signal.map (fun (x : BitVec 64) => (x >>> 16) &&& 1#64) sum1)
  
  -- Pipeline a2, b2 for stage 2
  let a2 := Signal.map (fun (x : BitVec 64) => (x >>> 16) &&& 0xFFFF#64) adda
  let b2 := Signal.map (fun (x : BitVec 64) => (x >>> 16) &&& 0xFFFF#64) addb
  let a2_ff1 := Signal.register (0#64) a2
  let b2_ff1 := Signal.register (0#64) b2
  
  -- Stage 2: compute bits [31:16]
  let stage2_en := Signal.register (0#1) stage1_en
  let sum2 : Signal dom (BitVec 64) := a2_ff1 + b2_ff1 + c1
  let s2 := Signal.register (0#64) (sum2 &&& (Signal.pure (0xFFFF#64) : Signal dom (BitVec 64)))
  let c2 := Signal.register (0#64) (Signal.map (fun (x : BitVec 64) => (x >>> 16) &&& 1#64) sum2)
  
  -- Pipeline a3, b3 for stage 3 (need 2 stages)
  let a3 := Signal.map (fun (x : BitVec 64) => (x >>> 32) &&& 0xFFFF#64) adda
  let b3 := Signal.map (fun (x : BitVec 64) => (x >>> 32) &&& 0xFFFF#64) addb
  let a3_ff1 := Signal.register (0#64) a3
  let b3_ff1 := Signal.register (0#64) b3
  let a3_ff2 := Signal.register (0#64) a3_ff1
  let b3_ff2 := Signal.register (0#64) b3_ff1
  
  -- Pipeline s1 forward
  let s1_ff1 := Signal.register (0#64) s1
  
  -- Stage 3: compute bits [47:32]
  let stage3_en := Signal.register (0#1) stage2_en
  let sum3 : Signal dom (BitVec 64) := a3_ff2 + b3_ff2 + c2
  let s3 := Signal.register (0#64) (sum3 &&& (Signal.pure (0xFFFF#64) : Signal dom (BitVec 64)))
  let c3 := Signal.register (0#64) (Signal.map (fun (x : BitVec 64) => (x >>> 16) &&& 1#64) sum3)
  
  -- Pipeline a4, b4 for stage 4 (need 3 stages)
  let a4 := Signal.map (fun (x : BitVec 64) => (x >>> 48) &&& 0xFFFF#64) adda
  let b4 := Signal.map (fun (x : BitVec 64) => (x >>> 48) &&& 0xFFFF#64) addb
  let a4_ff1 := Signal.register (0#64) a4
  let b4_ff1 := Signal.register (0#64) b4
  let a4_ff2 := Signal.register (0#64) a4_ff1
  let b4_ff2 := Signal.register (0#64) b4_ff1
  let a4_ff3 := Signal.register (0#64) a4_ff2
  let b4_ff3 := Signal.register (0#64) b4_ff2
  
  -- Pipeline s1, s2 forward
  let s1_ff2 := Signal.register (0#64) s1_ff1
  let s2_ff1 := Signal.register (0#64) s2
  
  -- Stage 4: compute bits [63:48]
  let o_en := Signal.register (0#1) stage3_en
  let sum4 : Signal dom (BitVec 64) := a4_ff3 + b4_ff3 + c3
  let s4 := Signal.register (0#64) (sum4 &&& (Signal.pure (0xFFFF#64) : Signal dom (BitVec 64)))
  let c4 := Signal.register (0#64) (Signal.map (fun (x : BitVec 64) => (x >>> 16) &&& 1#64) sum4)
  
  -- Pipeline s1, s2, s3 to align with s4
  let s1_ff3 := Signal.register (0#64) s1_ff2
  let s2_ff2 := Signal.register (0#64) s2_ff1
  let s3_ff1 := Signal.register (0#64) s3
  
  -- Assemble result: {c4, s4, s3_ff1, s2_ff2, s1_ff3}
  -- Convert each 64-bit piece to 65-bit and combine
  let s1_65 : Signal dom (BitVec 65) := Signal.map (fun (x : BitVec 64) => x.zeroExtend 65) s1_ff3
  let s2_65 : Signal dom (BitVec 65) := Signal.map (fun (x : BitVec 64) => (x.zeroExtend 65) <<< 16) s2_ff2
  let s3_65 : Signal dom (BitVec 65) := Signal.map (fun (x : BitVec 64) => (x.zeroExtend 65) <<< 32) s3_ff1
  let s4_65 : Signal dom (BitVec 65) := Signal.map (fun (x : BitVec 64) => (x.zeroExtend 65) <<< 48) s4
  let c4_65 : Signal dom (BitVec 65) := Signal.map (fun (x : BitVec 64) => (x.zeroExtend 65) <<< 64) c4
  let result : Signal dom (BitVec 65) := s1_65 ||| s2_65 ||| s3_65 ||| s4_65 ||| c4_65
  
  bundle2 result o_en

#synthesizeVerilog adder_pipe_64bit
