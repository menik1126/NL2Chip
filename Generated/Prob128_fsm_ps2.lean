import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding
private abbrev stBYTE1 : BitVec 2 := 0#2
private abbrev stBYTE2 : BitVec 2 := 1#2
private abbrev stBYTE3 : BitVec 2 := 2#2
private abbrev stDONE  : BitVec 2 := 3#2

/-- PS/2 mouse protocol FSM: searches for 3-byte message boundaries.
    First byte has in[3]=1. Signals done after receiving all 3 bytes. -/
def prob128_fsm_ps2 {dom : DomainConfig}
    (reset : Signal dom Bool)
    (inp : Signal dom (BitVec 8))
    : Signal dom (BitVec 1) :=
  -- State register via Signal.loop
  let state : Signal dom (BitVec 2) :=
    Signal.loop fun (state : Signal dom (BitVec 2)) =>
      -- Extract in[3] by masking with 0b00001000 = 8
      let bit3 := inp &&& (8#8 : BitVec 8)
      let in3 : Signal dom Bool := bit3 === (8#8 : BitVec 8)
      
      -- Next state logic based on current state
      let isBYTE1 : Signal dom Bool := state === Signal.pure stBYTE1
      let isBYTE2 : Signal dom Bool := state === Signal.pure stBYTE2
      let isBYTE3 : Signal dom Bool := state === Signal.pure stBYTE3
      
      -- BYTE1: if in[3]=1 then BYTE2 else BYTE1
      let nextFromBYTE1 : Signal dom (BitVec 2) := Signal.mux in3 (Signal.pure stBYTE2) (Signal.pure stBYTE1)
      -- BYTE2: always go to BYTE3
      let nextFromBYTE2 : Signal dom (BitVec 2) := Signal.pure stBYTE3
      -- BYTE3: always go to DONE
      let nextFromBYTE3 : Signal dom (BitVec 2) := Signal.pure stDONE
      -- DONE: if in[3]=1 then BYTE2 else BYTE1
      let nextFromDONE : Signal dom (BitVec 2) := Signal.mux in3 (Signal.pure stBYTE2) (Signal.pure stBYTE1)
      
      -- Mux chain to select next state
      let temp1 : Signal dom (BitVec 2) := Signal.mux isBYTE3 nextFromBYTE3 nextFromDONE
      let temp2 : Signal dom (BitVec 2) := Signal.mux isBYTE2 nextFromBYTE2 temp1
      let nextState : Signal dom (BitVec 2) := Signal.mux isBYTE1 nextFromBYTE1 temp2
      
      -- Apply synchronous reset: reset → BYTE1
      let nextWithReset : Signal dom (BitVec 2) := Signal.mux reset (Signal.pure stBYTE1) nextState
      
      -- Register with initial value BYTE1
      Signal.register stBYTE1 nextWithReset
  
  -- Output: done = (state == DONE)
  Signal.mux (state === Signal.pure stDONE) (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob128_fsm_ps2
