import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Pipelined 8-bit unsigned multiplier with 3-stage pipeline -/
def multi_pipe_8bit {dom : DomainConfig}
    (mul_en_in : Signal dom Bool)
    (mul_a : Signal dom (BitVec 8))
    (mul_b : Signal dom (BitVec 8))
    : Signal dom (BitVec 1 × BitVec 16) :=
  
  -- Enable pipeline: 3-stage shift register
  let en_stage := Signal.loop fun (en_reg : Signal dom (BitVec 3)) =>
    let en_bit := Signal.mux mul_en_in (Signal.pure 1#1) (Signal.pure 0#1)
    let en_bit_ext := Signal.map (fun b => b.zeroExtend 3) en_bit
    let shifted := en_reg <<< 1#3
    let next_en := shifted ||| en_bit_ext
    Signal.register 0#3 next_en
  
  let mul_en_out := (en_stage &&& 4#3) === 4#3
  
  -- Stage 0: Input registers (only update when mul_en_in is active)
  let mul_a_reg := Signal.register 0#8 (Signal.mux mul_en_in mul_a (Signal.pure 0#8))
  let mul_b_reg := Signal.register 0#8 (Signal.mux mul_en_in mul_b (Signal.pure 0#8))
  
  -- Stage 1: Generate partial products (combinational)
  -- Extract each bit of mul_b_reg as Bool
  let b0 := (mul_b_reg &&& 1#8) === 1#8
  let b1 := (mul_b_reg &&& 2#8) === 2#8
  let b2 := (mul_b_reg &&& 4#8) === 4#8
  let b3 := (mul_b_reg &&& 8#8) === 8#8
  let b4 := (mul_b_reg &&& 16#8) === 16#8
  let b5 := (mul_b_reg &&& 32#8) === 32#8
  let b6 := (mul_b_reg &&& 64#8) === 64#8
  let b7 := (mul_b_reg &&& 128#8) === 128#8
  
  -- Extend mul_a_reg to 16 bits
  let a_ext := Signal.map (fun a => a.zeroExtend 16) mul_a_reg
  
  -- Pre-compute all shifted versions
  let a_shl1 := a_ext <<< 1#16
  let a_shl2 := a_ext <<< 2#16
  let a_shl3 := a_ext <<< 3#16
  let a_shl4 := a_ext <<< 4#16
  let a_shl5 := a_ext <<< 5#16
  let a_shl6 := a_ext <<< 6#16
  let a_shl7 := a_ext <<< 7#16
  
  -- Generate partial products with mux
  let temp0 := Signal.mux b0 a_ext (Signal.pure 0#16)
  let temp1 := Signal.mux b1 a_shl1 (Signal.pure 0#16)
  let temp2 := Signal.mux b2 a_shl2 (Signal.pure 0#16)
  let temp3 := Signal.mux b3 a_shl3 (Signal.pure 0#16)
  let temp4 := Signal.mux b4 a_shl4 (Signal.pure 0#16)
  let temp5 := Signal.mux b5 a_shl5 (Signal.pure 0#16)
  let temp6 := Signal.mux b6 a_shl6 (Signal.pure 0#16)
  let temp7 := Signal.mux b7 a_shl7 (Signal.pure 0#16)
  
  -- Stage 2: Partial sums (register pairs of partial products)
  let sum0 := Signal.register 0#16 (temp0 + temp1)
  let sum1 := Signal.register 0#16 (temp2 + temp3)
  let sum2 := Signal.register 0#16 (temp4 + temp5)
  let sum3 := Signal.register 0#16 (temp6 + temp7)
  
  -- Stage 3: Final sum
  let mul_out_reg := Signal.register 0#16 (sum0 + sum1 + sum2 + sum3)
  
  -- Output gating: output mul_out_reg when enabled, else 0
  let mul_out := Signal.register 0#16 
    (Signal.mux mul_en_out mul_out_reg (Signal.pure 0#16))
  
  -- Convert mul_en_out Bool to BitVec 1
  let mul_en_out_bv := Signal.mux mul_en_out (Signal.pure 1#1) (Signal.pure 0#1)
  
  bundle2 mul_en_out_bv mul_out

#synthesizeVerilog multi_pipe_8bit
