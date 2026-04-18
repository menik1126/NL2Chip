import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- FSM one-hot state transition and output logic -/
def prob143_fsm_onehot {dom : DomainConfig}
    (inp : Signal dom Bool) (state : Signal dom (BitVec 10))
    : Signal dom (BitVec 10 × (BitVec 1 × BitVec 1)) :=
  
  Signal.map (fun pair =>
    let i := pair.1
    let s := pair.2
    -- Extract individual state bits
    let s0 := s.getLsb 0
    let s1 := s.getLsb 1
    let s2 := s.getLsb 2
    let s3 := s.getLsb 3
    let s4 := s.getLsb 4
    let s5 := s.getLsb 5
    let s6 := s.getLsb 6
    let s7 := s.getLsb 7
    let s8 := s.getLsb 8
    let s9 := s.getLsb 9
    
    -- Compute outputs
    let out1 := if s8 || s9 then 1#1 else 0#1
    let out2 := if s7 || s9 then 1#1 else 0#1
    
    -- Compute next_state bits
    let not_i := !i
    
    let ns0 := if not_i && (s0 || s1 || s2 || s3 || s4 || s7 || s8 || s9) then 1#1 else 0#1
    let ns1 := if i && (s0 || s8 || s9) then 1#1 else 0#1
    let ns2 := if i && s1 then 1#1 else 0#1
    let ns3 := if i && s2 then 1#1 else 0#1
    let ns4 := if i && s3 then 1#1 else 0#1
    let ns5 := if i && s4 then 1#1 else 0#1
    let ns6 := if i && s5 then 1#1 else 0#1
    let ns7 := if i && (s6 || s7) then 1#1 else 0#1
    let ns8 := if not_i && s5 then 1#1 else 0#1
    let ns9 := if not_i && s6 then 1#1 else 0#1
    
    -- Build 10-bit next_state
    let next_state := ns0 ++ ns1 ++ ns2 ++ ns3 ++ ns4 ++ ns5 ++ ns6 ++ ns7 ++ ns8 ++ ns9
    
    (next_state, (out1, out2))
  ) (bundle2 inp state)

#synthesizeVerilog prob143_fsm_onehot
