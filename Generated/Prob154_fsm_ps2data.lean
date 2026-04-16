import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- FSM state encoding (2 bits)
private abbrev stBYTE1 : BitVec 2 := 0#2  -- Waiting for byte 1 (need in[3]=1)
private abbrev stBYTE2 : BitVec 2 := 1#2  -- Received byte 1, waiting for byte 2
private abbrev stBYTE3 : BitVec 2 := 2#2  -- Received byte 2, waiting for byte 3
private abbrev stDONE  : BitVec 2 := 3#2  -- All 3 bytes received (output done)

/-- PS2 message boundary FSM with datapath.
    Searches for message boundaries by discarding bytes until in[3]=1,
    then captures 3 bytes and asserts done. Active-high synchronous reset.
    Outputs: (done, out_bytes[23:0]) where out_bytes[23:16]=byte1, [15:8]=byte2, [7:0]=byte3. -/
def prob154_fsm_ps2data {dom : DomainConfig}
    (reset : Signal dom Bool)
    (in_  : Signal dom (BitVec 8))
    : Signal dom (BitVec 1 × BitVec 24) :=
  -- State pair: (FSM state [1:0], data register [23:0])
  -- Use Signal.loop with a pair type; Sparkle represents pairs as concatenated BitVec
  let stateAndData : Signal dom (BitVec 2 × BitVec 24) :=
    Signal.loop fun (sd : Signal dom (BitVec 2 × BitVec 24)) =>
      -- Extract FSM state (high 2 bits) and data (low 24 bits)
      let fsmState : Signal dom (BitVec 2)  := Signal.map Prod.fst sd
      let dataReg  : Signal dom (BitVec 24) := Signal.map Prod.snd sd

      -- Check in_[3]: shift right by 3, mask to 1 bit, compare with 1
      let in3shifted : Signal dom (BitVec 8) := (in_ >>> 3#8) &&& 1#8
      let in3 : Signal dom Bool := in3shifted === 1#8

      -- State comparisons
      let isBYTE1 := fsmState === Signal.pure stBYTE1
      let isBYTE2 := fsmState === Signal.pure stBYTE2
      let isBYTE3 := fsmState === Signal.pure stBYTE3
      -- isDONE is implicit default

      -- Next state logic:
      --   BYTE1: in3 ? BYTE2 : BYTE1
      --   BYTE2: BYTE3
      --   BYTE3: DONE
      --   DONE:  in3 ? BYTE2 : BYTE1
      let nextFromBYTE1 := Signal.mux in3 (Signal.pure stBYTE2) (Signal.pure stBYTE1)
      let nextState :=
        Signal.mux isBYTE1 nextFromBYTE1
          (Signal.mux isBYTE2 (Signal.pure stBYTE3)
            (Signal.mux isBYTE3 (Signal.pure stDONE)
              -- DONE state: same as BYTE1 logic
              (Signal.mux in3 (Signal.pure stBYTE2) (Signal.pure stBYTE1))))

      -- Apply synchronous reset: reset → BYTE1
      let nextStateWithReset := Signal.mux reset (Signal.pure stBYTE1) nextState

      -- Data shift register: new_data[23:0] = {dataReg[15:0], in_[7:0]}
      -- Get lower 16 bits of data: dataReg & 0xFFFF
      let dataLow16 : Signal dom (BitVec 24) := dataReg &&& 0xFFFF#24
      -- Zero-extend in_ from 8 to 24 bits using concatenation: {16'b0, in_}
      let zeros16 : Signal dom (BitVec 16) := Signal.pure 0#16
      let in8ext    : Signal dom (BitVec 24) := zeros16 ++ in_
      -- New data = (lower16 << 8) | in_ext
      let newData   : Signal dom (BitVec 24) := (dataLow16 <<< 8#24) ||| in8ext

      -- Register both state and data
      let regState := Signal.register stBYTE1 nextStateWithReset
      let regData  := Signal.register 0#24 newData

      bundle2 regState regData

  -- Extract outputs
  let fsmState : Signal dom (BitVec 2)  := Signal.map Prod.fst stateAndData
  let dataOut  : Signal dom (BitVec 24) := Signal.map Prod.snd stateAndData

  -- done = (state == DONE)
  let isDone  := fsmState === Signal.pure stDONE
  let doneOut : Signal dom (BitVec 1) := Signal.mux isDone (Signal.pure 1#1) (Signal.pure 0#1)

  bundle2 doneOut dataOut

#synthesizeVerilog prob154_fsm_ps2data
