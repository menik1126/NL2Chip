import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- FSM one-hot state transition logic only (test). -/
def prob143_fsm_onehot_test {dom : DomainConfig}
    (inp : Signal dom Bool)
    (state : Signal dom (BitVec 10))
    : Signal dom (BitVec 10) :=
  -- Extract individual state bits as Bool signals
  let s0 := Signal.map (fun x => x.getLsb 0) state
  let s1 := Signal.map (fun x => x.getLsb 1) state
  let s2 := Signal.map (fun x => x.getLsb 2) state
  let s3 := Signal.map (fun x => x.getLsb 3) state
  let s4 := Signal.map (fun x => x.getLsb 4) state
  let s5 := Signal.map (fun x => x.getLsb 5) state
  let s6 := Signal.map (fun x => x.getLsb 6) state
  let s7 := Signal.map (fun x => x.getLsb 7) state
  let s8 := Signal.map (fun x => x.getLsb 8) state
  let s9 := Signal.map (fun x => x.getLsb 9) state
  
  let not_in := ~~~inp
  
  -- Next state logic - compute each bit as a Signal
  let ns0_cond := s0 ||| s1 ||| s2 ||| s3 ||| s4 ||| s7 ||| s8 ||| s9
  let ns0_bool := not_in &&& ns0_cond
  let ns0 := Signal.mux ns0_bool (Signal.pure 1#1) (Signal.pure 0#1)
  
  let ns1_cond := s0 ||| s8 ||| s9
  let ns1_bool := inp &&& ns1_cond
  let ns1 := Signal.mux ns1_bool (Signal.pure 1#1) (Signal.pure 0#1)
  
  let ns2_bool := inp &&& s1
  let ns2 := Signal.mux ns2_bool (Signal.pure 1#1) (Signal.pure 0#1)
  
  let ns3_bool := inp &&& s2
  let ns3 := Signal.mux ns3_bool (Signal.pure 1#1) (Signal.pure 0#1)
  
  let ns4_bool := inp &&& s3
  let ns4 := Signal.mux ns4_bool (Signal.pure 1#1) (Signal.pure 0#1)
  
  let ns5_bool := inp &&& s4
  let ns5 := Signal.mux ns5_bool (Signal.pure 1#1) (Signal.pure 0#1)
  
  let ns6_bool := inp &&& s5
  let ns6 := Signal.mux ns6_bool (Signal.pure 1#1) (Signal.pure 0#1)
  
  let ns7_cond := s6 ||| s7
  let ns7_bool := inp &&& ns7_cond
  let ns7 := Signal.mux ns7_bool (Signal.pure 1#1) (Signal.pure 0#1)
  
  let ns8_bool := not_in &&& s5
  let ns8 := Signal.mux ns8_bool (Signal.pure 1#1) (Signal.pure 0#1)
  
  let ns9_bool := not_in &&& s6
  let ns9 := Signal.mux ns9_bool (Signal.pure 1#1) (Signal.pure 0#1)
  
  -- Build 10-bit result using Signal.map
  Signal.map (fun (b0, b1, b2, b3, b4, b5, b6, b7, b8, b9) =>
    b0 ++ b1 ++ b2 ++ b3 ++ b4 ++ b5 ++ b6 ++ b7 ++ b8 ++ b9
  ) (bundle2 ns0 (bundle2 ns1 (bundle2 ns2 (bundle2 ns3 (bundle2 ns4 
    (bundle2 ns5 (bundle2 ns6 (bundle2 ns7 (bundle2 ns8 ns9)))))))))

#synthesizeVerilog prob143_fsm_onehot_test
