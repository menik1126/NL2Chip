import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- One-hot FSM state transition and output logic for a 10-state machine.
    Inputs: in (1-bit Bool), state (10-bit one-hot encoded)
    Outputs: next_state (10-bit), out1 (1-bit), out2 (1-bit)

    State machine outputs (out1, out2):
      S7 → (0, 1), S8 → (1, 0), S9 → (1, 1), others → (0, 0)

    Next state logic:
      ns[0] = !in && (s[0..4] | s[7] | s[8] | s[9])
      ns[1] = in && (s[0] | s[8] | s[9])
      ns[2] = in && s[1]
      ns[3] = in && s[2]
      ns[4] = in && s[3]
      ns[5] = in && s[4]
      ns[6] = in && s[5]
      ns[7] = in && (s[6] | s[7])
      ns[8] = !in && s[5]
      ns[9] = !in && s[6] -/
def prob143_fsm_onehot {dom : DomainConfig}
    (inp : Signal dom Bool)
    (state : Signal dom (BitVec 10))
    : Signal dom (BitVec 10 × BitVec 1 × BitVec 1) :=
  -- Extract individual state bits as BitVec 1 signals using extractLsb'
  let s0 : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 0 1) state
  let s1 : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 1 1) state
  let s2 : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 2 1) state
  let s3 : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 3 1) state
  let s4 : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 4 1) state
  let s5 : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 5 1) state
  let s6 : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 6 1) state
  let s7 : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 7 1) state
  let s8 : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 8 1) state
  let s9 : Signal dom (BitVec 1) := Signal.map (fun x => x.extractLsb' 9 1) state

  -- Convert Bool inp to BitVec 1 for bitwise operations
  let i : Signal dom (BitVec 1) := Signal.mux inp (Signal.pure 1#1) (Signal.pure 0#1)
  let ni : Signal dom (BitVec 1) := ~~~i  -- NOT in

  -- Output logic
  -- out1 = state[8] | state[9]  (S8 and S9 have out1=1)
  let out1 : Signal dom (BitVec 1) := s8 ||| s9

  -- out2 = state[7] | state[9]  (S7 and S9 have out2=1)
  let out2 : Signal dom (BitVec 1) := s7 ||| s9

  -- Next state logic (each bit as BitVec 1)
  -- ns[0] = !in && (s0|s1|s2|s3|s4|s7|s8|s9)
  let ns0 : Signal dom (BitVec 1) :=
    ni &&& (s0 ||| s1 ||| s2 ||| s3 ||| s4 ||| s7 ||| s8 ||| s9)

  -- ns[1] = in && (s0 | s8 | s9)
  let ns1 : Signal dom (BitVec 1) := i &&& (s0 ||| s8 ||| s9)

  -- ns[2] = in && s1
  let ns2 : Signal dom (BitVec 1) := i &&& s1

  -- ns[3] = in && s2
  let ns3 : Signal dom (BitVec 1) := i &&& s2

  -- ns[4] = in && s3
  let ns4 : Signal dom (BitVec 1) := i &&& s3

  -- ns[5] = in && s4
  let ns5 : Signal dom (BitVec 1) := i &&& s4

  -- ns[6] = in && s5
  let ns6 : Signal dom (BitVec 1) := i &&& s5

  -- ns[7] = in && (s6 | s7)
  let ns7 : Signal dom (BitVec 1) := i &&& (s6 ||| s7)

  -- ns[8] = !in && s5
  let ns8 : Signal dom (BitVec 1) := ni &&& s5

  -- ns[9] = !in && s6
  let ns9 : Signal dom (BitVec 1) := ni &&& s6

  -- Assemble 10-bit next_state by zero-extending each bit and shifting to position
  let ns0e : Signal dom (BitVec 10) := Signal.map (fun b => b.zeroExtend 10) ns0
  let ns1e : Signal dom (BitVec 10) := (Signal.map (fun b => b.zeroExtend 10) ns1) <<< (1#10 : BitVec 10)
  let ns2e : Signal dom (BitVec 10) := (Signal.map (fun b => b.zeroExtend 10) ns2) <<< (2#10 : BitVec 10)
  let ns3e : Signal dom (BitVec 10) := (Signal.map (fun b => b.zeroExtend 10) ns3) <<< (3#10 : BitVec 10)
  let ns4e : Signal dom (BitVec 10) := (Signal.map (fun b => b.zeroExtend 10) ns4) <<< (4#10 : BitVec 10)
  let ns5e : Signal dom (BitVec 10) := (Signal.map (fun b => b.zeroExtend 10) ns5) <<< (5#10 : BitVec 10)
  let ns6e : Signal dom (BitVec 10) := (Signal.map (fun b => b.zeroExtend 10) ns6) <<< (6#10 : BitVec 10)
  let ns7e : Signal dom (BitVec 10) := (Signal.map (fun b => b.zeroExtend 10) ns7) <<< (7#10 : BitVec 10)
  let ns8e : Signal dom (BitVec 10) := (Signal.map (fun b => b.zeroExtend 10) ns8) <<< (8#10 : BitVec 10)
  let ns9e : Signal dom (BitVec 10) := (Signal.map (fun b => b.zeroExtend 10) ns9) <<< (9#10 : BitVec 10)

  let next_state : Signal dom (BitVec 10) :=
    ns0e ||| ns1e ||| ns2e ||| ns3e ||| ns4e |||
    ns5e ||| ns6e ||| ns7e ||| ns8e ||| ns9e

  bundle2 next_state (bundle2 out1 out2)

#synthesizeVerilog prob143_fsm_onehot
