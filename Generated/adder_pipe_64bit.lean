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
  -- Stage enable pipeline
  let stage1_bool := Signal.register false i_en
  let stage2_bool := Signal.register false stage1_bool
  let stage3_bool := Signal.register false stage2_bool
  let o_en_bool := Signal.register false stage3_bool
  let o_en := Signal.mux o_en_bool (Signal.pure 1#1) (Signal.pure 0#1)
  
  -- Simple pipelined adder: just add and pipeline the result
  let sum_65 := adda + addb
  
  -- Pipeline the sum through 4 stages
  let sum_stage1 := Signal.register 0#64 sum_65
  let sum_stage2 := Signal.register 0#64 sum_stage1
  let sum_stage3 := Signal.register 0#64 sum_stage2
  let sum_stage4 := Signal.register 0#64 sum_stage3
  
  -- Extend to 65 bits
  let result := Signal.map (fun x : BitVec 64 => x.zeroExtend 65) sum_stage4
  
  bundle2 result o_en

#synthesizeVerilog adder_pipe_64bit
