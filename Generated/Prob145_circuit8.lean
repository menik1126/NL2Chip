import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Sequential circuit with combinational output p and negative-edge triggered output q -/
def prob145_circuit8 {dom : DomainConfig}
    (clock : Signal dom (BitVec 1))
    (a : Signal dom (BitVec 1))
    : Signal dom (BitVec 1 × BitVec 1) :=
  -- p is combinational: p = clock AND a
  let p := clock &&& a
  
  -- q is negative-edge triggered: sample a on falling edge of clock
  -- State: 2-bit [q_value, prev_clock]
  let state := Signal.loop fun state =>
    let q_val := Signal.map (fun s : BitVec 2 => s.extractLsb 1 1) state
    let prev_clock := Signal.map (fun s : BitVec 2 => s.extractLsb 0 0) state
    
    -- Detect negative edge: prev_clock=1 AND clock=0
    let neg_edge := (~~~clock) &&& prev_clock
    
    -- Update q on negative edge
    let next_q := Signal.mux (neg_edge === Signal.pure 1#1) a q_val
    
    -- Pack next state: [next_q, clock]
    let packed := bundle2 next_q clock
    let next_state := Signal.map (fun (pair : BitVec 1 × BitVec 1) => 
      pair.1 ++ pair.2) packed
    
    Signal.register 0#2 next_state
  
  let q := Signal.map (fun s : BitVec 2 => s.extractLsb 1 1) state
  
  bundle2 p q

#synthesizeVerilog prob145_circuit8
