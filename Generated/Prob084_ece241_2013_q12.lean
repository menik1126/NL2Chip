import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 8x1 memory with shift register write and random access read.
    S shifts into Q[0] when enable is high. ABC selects which Q bit outputs to Z. -/
def prob084_ece241_2013_q12 {dom : DomainConfig}
    (enable : Signal dom Bool)
    (S : Signal dom (BitVec 1))
    (A B C : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  -- 8-bit shift register: Q[7:0], S shifts into Q[0]
  let q := Signal.loop fun (q : Signal dom (BitVec 8)) =>
    let shifted := (q <<< 1#8) ||| Signal.map (fun s => s.zeroExtend 8) S
    let q_next := Signal.mux enable shifted q
    Signal.register 0#8 q_next
  
  -- Extract bits using shifts - work with BitVec 8 throughout
  let q0_8 := q &&& 1#8
  let q1_8 := (q >>> 1#8) &&& 1#8
  let q2_8 := (q >>> 2#8) &&& 1#8
  let q3_8 := (q >>> 3#8) &&& 1#8
  let q4_8 := (q >>> 4#8) &&& 1#8
  let q5_8 := (q >>> 5#8) &&& 1#8
  let q6_8 := (q >>> 6#8) &&& 1#8
  let q7_8 := (q >>> 7#8) &&& 1#8
  
  -- Build mux tree with BitVec 8
  let mux01_8 := Signal.mux C q1_8 q0_8
  let mux23_8 := Signal.mux C q3_8 q2_8
  let mux45_8 := Signal.mux C q5_8 q4_8
  let mux67_8 := Signal.mux C q7_8 q6_8
  
  let mux03_8 := Signal.mux B mux23_8 mux01_8
  let mux47_8 := Signal.mux B mux67_8 mux45_8
  
  let result_8 := Signal.mux A mux47_8 mux03_8
  
  -- Extract LSB as BitVec 1
  Signal.map (fun (v : BitVec 8) => v.extractLsb 0 0) result_8

#synthesizeVerilog prob084_ece241_2013_q12
