import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding: BYTE1=0, BYTE2=1, BYTE3=2, DONE=3
private abbrev stBYTE1 : BitVec 2 := 0#2
private abbrev stBYTE2 : BitVec 2 := 1#2
private abbrev stBYTE3 : BitVec 2 := 2#2
private abbrev stDONE  : BitVec 2 := 3#2

/-- PS/2 mouse protocol FSM: searches for 3-byte message boundaries.
    First byte must have in[3]=1. Asserts done one cycle after the
    third byte is received. Synchronous active-high reset. -/
def prob128_fsm_ps2 {dom : DomainConfig}
    (reset : Signal dom Bool)
    (inp : Signal dom (BitVec 8))
    : Signal dom (BitVec 1) :=
  -- Extract in[3]: mask bit 3 (value 8) and check if nonzero
  -- in3 = (inp & 0x08) == 0x08
  let in3 : Signal dom Bool := (inp &&& (8#8 : BitVec 8)) === (8#8 : BitVec 8)
  -- Compute state using Signal.loop for feedback
  let state : Signal dom (BitVec 2) :=
    Signal.loop fun (state : Signal dom (BitVec 2)) =>
      -- State comparisons
      let isBYTE1 := state === Signal.pure stBYTE1
      let isBYTE2 := state === Signal.pure stBYTE2
      let isBYTE3 := state === Signal.pure stBYTE3
      -- Next state per state:
      -- BYTE1: in[3]=1 → BYTE2, else → BYTE1
      let nextFromBYTE1 := Signal.mux in3 (Signal.pure stBYTE2) (Signal.pure stBYTE1)
      -- BYTE2: always → BYTE3
      let nextFromBYTE2 := Signal.pure stBYTE3
      -- BYTE3: always → DONE
      let nextFromBYTE3 := Signal.pure stDONE
      -- DONE: in[3]=1 → BYTE2, else → BYTE1
      let nextFromDONE  := Signal.mux in3 (Signal.pure stBYTE2) (Signal.pure stBYTE1)
      -- Select next state based on current state
      let nextState :=
        Signal.mux isBYTE1 nextFromBYTE1
          (Signal.mux isBYTE2 nextFromBYTE2
            (Signal.mux isBYTE3 nextFromBYTE3
              nextFromDONE))
      -- Apply synchronous reset: reset → BYTE1
      let nextWithReset := Signal.mux reset (Signal.pure stBYTE1) nextState
      -- State register initialized to BYTE1
      Signal.register stBYTE1 nextWithReset
  -- Output: done = (state == DONE)
  Signal.mux (state === Signal.pure stDONE) (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob128_fsm_ps2
