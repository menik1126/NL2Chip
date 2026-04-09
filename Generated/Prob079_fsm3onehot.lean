import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- FSM state transition and output logic with one-hot encoding. 
    A=0001, B=0010, C=0100, D=1000. Returns (next_state, out). -/
def prob079_fsm3onehot {dom : DomainConfig}
    (inp : Signal dom Bool) (state : Signal dom (BitVec 4))
    : Signal dom (BitVec 4 × BitVec 1) :=
  
  -- From reference Verilog:
  -- next_state[0] = (state[0] | state[2]) & ~in;  // A bit
  -- next_state[1] = (state[0] | state[1] | state[3]) & in;  // B bit
  -- next_state[2] = (state[1] | state[3]) & ~in;  // C bit
  -- next_state[3] = state[2] & in;  // D bit
  -- out = state[3];
  
  -- Extract state bits using bitwise AND with masks  
  let stateA := (state &&& 1#4) === 1#4   -- bit 0 (A=0001)
  let stateB := (state &&& 2#4) === 2#4   -- bit 1 (B=0010)
  let stateC := (state &&& 4#4) === 4#4   -- bit 2 (C=0100)
  let stateD := (state &&& 8#4) === 8#4   -- bit 3 (D=1000)
  
  -- Negate input for ~in
  let notInp := Signal.map not inp
  
  -- Compute next state bits as boolean signals
  let nextA := (stateA ||| stateC) &&& notInp
  let nextB := (stateA ||| stateB ||| stateD) &&& inp
  let nextC := (stateB ||| stateD) &&& notInp
  let nextD := stateC &&& inp
  
  -- Convert boolean signals to BitVec and combine using mux chains
  let ns0 := Signal.mux nextA (Signal.pure 1#4) (Signal.pure 0#4)
  let ns1 := Signal.mux nextB (Signal.pure 2#4) (Signal.pure 0#4)
  let ns2 := Signal.mux nextC (Signal.pure 4#4) (Signal.pure 0#4)
  let ns3 := Signal.mux nextD (Signal.pure 8#4) (Signal.pure 0#4)
  let nextState := ns0 ||| ns1 ||| ns2 ||| ns3
  
  -- Output is state[3] (D bit)
  let out := Signal.mux stateD (Signal.pure 1#1) (Signal.pure 0#1)
  
  bundle2 nextState out

#synthesizeVerilog prob079_fsm3onehot