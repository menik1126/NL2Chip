import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Replicate a BitVec 1 to an 8-bit mask using negation -/
def bitToMask8 (b : BitVec 1) : BitVec 8 :=
  -(b.zeroExtend 8)

/-- 4-bit pipelined multiplier with two pipeline stages -/
def multi_pipe_4bit {dom : DomainConfig}
    (rst_n : Signal dom Bool)
    (mul_a mul_b : Signal dom (BitVec 4))
    : Signal dom (BitVec 8) :=
  -- Compute partial product 0
  let p0 := Signal.map (fun (ab : BitVec 4 × BitVec 4) =>
    let a_ext : BitVec 8 := ab.1.zeroExtend 8
    let b0 := ab.2.extractLsb 0 0
    a_ext &&& (bitToMask8 b0)
  ) (bundle2 mul_a mul_b)
  
  -- Compute partial product 1
  let p1 := Signal.map (fun (ab : BitVec 4 × BitVec 4) =>
    let a_ext : BitVec 8 := ab.1.zeroExtend 8
    let b1 := ab.2.extractLsb 1 1
    (a_ext.shiftLeft 1) &&& (bitToMask8 b1)
  ) (bundle2 mul_a mul_b)
  
  -- Compute partial product 2
  let p2 := Signal.map (fun (ab : BitVec 4 × BitVec 4) =>
    let a_ext : BitVec 8 := ab.1.zeroExtend 8
    let b2 := ab.2.extractLsb 2 2
    (a_ext.shiftLeft 2) &&& (bitToMask8 b2)
  ) (bundle2 mul_a mul_b)
  
  -- Compute partial product 3
  let p3 := Signal.map (fun (ab : BitVec 4 × BitVec 4) =>
    let a_ext : BitVec 8 := ab.1.zeroExtend 8
    let b3 := ab.2.extractLsb 3 3
    (a_ext.shiftLeft 3) &&& (bitToMask8 b3)
  ) (bundle2 mul_a mul_b)
  
  -- Pipeline stage 1: register partial sums
  let s1_next := p0 + p1
  let s2_next := p2 + p3
  let s1 := Signal.register 0#8 (Signal.mux rst_n s1_next (Signal.pure 0#8))
  let s2 := Signal.register 0#8 (Signal.mux rst_n s2_next (Signal.pure 0#8))
  
  -- Pipeline stage 2: register final product
  let out_next := s1 + s2
  Signal.register 0#8 (Signal.mux rst_n out_next (Signal.pure 0#8))

#synthesizeVerilog multi_pipe_4bit
