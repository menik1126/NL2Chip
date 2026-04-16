import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 8x1 memory implemented as shift register with random access read.
    S shifts into Q[0] (MSB first) when enable is high.
    ABC selects which Q bit to output on Z. -/
def prob084_ece241_2013_q12 {dom : DomainConfig}
    (enable : Signal dom Bool)
    (S : Signal dom (BitVec 1))
    (A : Signal dom (BitVec 1))
    (B : Signal dom (BitVec 1))
    (C : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  -- 8-bit shift register: Q[7:0]
  -- When enable, shift left: Q <= {Q[6:0], S}
  let q := Signal.loop fun (q : Signal dom (BitVec 8)) =>
    -- Shift left: concatenate q[6:0] with S
    let shifted := q <<< 1#8
    let newQ := (fun sh sv => sh ||| (sv.zeroExtend 8)) <$> shifted <*> S
    let nextQ := Signal.mux enable newQ q
    Signal.register 0#8 nextQ
  
  -- Extract individual bits from q
  let q0 := Signal.map (fun qv => (qv.extractLsb' 0 1)) q
  let q1 := Signal.map (fun qv => (qv.extractLsb' 1 1)) q
  let q2 := Signal.map (fun qv => (qv.extractLsb' 2 1)) q
  let q3 := Signal.map (fun qv => (qv.extractLsb' 3 1)) q
  let q4 := Signal.map (fun qv => (qv.extractLsb' 4 1)) q
  let q5 := Signal.map (fun qv => (qv.extractLsb' 5 1)) q
  let q6 := Signal.map (fun qv => (qv.extractLsb' 6 1)) q
  let q7 := Signal.map (fun qv => (qv.extractLsb' 7 1)) q
  
  -- Convert A, B, C to Bool signals
  let aBool := A === (1#1 : BitVec 1)
  let bBool := B === (1#1 : BitVec 1)
  let cBool := C === (1#1 : BitVec 1)
  
  -- Build 8-to-1 mux tree: ABC selects from q[0..7]
  -- When ABC=000, select q0; when ABC=001, select q1, etc.
  let mux01 := Signal.mux cBool q1 q0
  let mux23 := Signal.mux cBool q3 q2
  let mux45 := Signal.mux cBool q5 q4
  let mux67 := Signal.mux cBool q7 q6
  let mux03 := Signal.mux bBool mux23 mux01
  let mux47 := Signal.mux bBool mux67 mux45
  Signal.mux aBool mux47 mux03

#synthesizeVerilog prob084_ece241_2013_q12
