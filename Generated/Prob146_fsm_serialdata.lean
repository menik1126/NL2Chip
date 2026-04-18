import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding (12 states, need 4 bits)
private abbrev stB0    : BitVec 4 := 0#4
private abbrev stB1    : BitVec 4 := 1#4
private abbrev stB2    : BitVec 4 := 2#4
private abbrev stB3    : BitVec 4 := 3#4
private abbrev stB4    : BitVec 4 := 4#4
private abbrev stB5    : BitVec 4 := 5#4
private abbrev stB6    : BitVec 4 := 6#4
private abbrev stB7    : BitVec 4 := 7#4
private abbrev stSTART : BitVec 4 := 8#4
private abbrev stSTOP  : BitVec 4 := 9#4
private abbrev stDONE  : BitVec 4 := 10#4
private abbrev stERR   : BitVec 4 := 11#4

/-- Serial data receiver FSM: detects start bit, captures 8 data bits (LSB first),
    verifies stop bit, and outputs the received byte. -/
def prob146_fsm_serialdata {dom : DomainConfig}
    (reset : Signal dom Bool)
    (inp : Signal dom Bool)
    : Signal dom (BitVec 8 × BitVec 1) :=
  -- State machine
  let state : Signal dom (BitVec 4) :=
    Signal.loop fun (state : Signal dom (BitVec 4)) =>
      -- Next state logic
      let isSTART := state === Signal.pure stSTART
      let isB0    := state === Signal.pure stB0
      let isB1    := state === Signal.pure stB1
      let isB2    := state === Signal.pure stB2
      let isB3    := state === Signal.pure stB3
      let isB4    := state === Signal.pure stB4
      let isB5    := state === Signal.pure stB5
      let isB6    := state === Signal.pure stB6
      let isB7    := state === Signal.pure stB7
      let isSTOP  := state === Signal.pure stSTOP
      let isDONE  := state === Signal.pure stDONE
      let isERR   := state === Signal.pure stERR
      
      -- Next state computation
      let nextState := 
        hw_cond stSTART
          | isSTART => Signal.mux inp (Signal.pure stSTART) (Signal.pure stB0)
          | isB0    => Signal.pure stB1
          | isB1    => Signal.pure stB2
          | isB2    => Signal.pure stB3
          | isB3    => Signal.pure stB4
          | isB4    => Signal.pure stB5
          | isB5    => Signal.pure stB6
          | isB6    => Signal.pure stB7
          | isB7    => Signal.pure stSTOP
          | isSTOP  => Signal.mux inp (Signal.pure stDONE) (Signal.pure stERR)
          | isDONE  => Signal.mux inp (Signal.pure stSTART) (Signal.pure stB0)
          | isERR   => Signal.mux inp (Signal.pure stSTART) (Signal.pure stERR)
      
      -- Apply reset
      let nextStateWithReset := Signal.mux reset (Signal.pure stSTART) nextState
      
      -- Register state
      Signal.register stSTART nextStateWithReset
  
  -- Shift register (separate from state machine)
  let shiftReg : Signal dom (BitVec 10) :=
    Signal.loop fun (shiftReg : Signal dom (BitVec 10)) =>
      let inBitVec := Signal.mux inp (Signal.pure 1#10) (Signal.pure 0#10)
      let shiftRegNext := (inBitVec <<< 9#10) ||| (shiftReg >>> 1#10)
      let shiftRegWithReset := Signal.mux reset (Signal.pure 0#10) shiftRegNext
      Signal.register 0#10 shiftRegWithReset
  
  -- Output logic
  let isDONE := state === Signal.pure stDONE
  let done := Signal.mux isDONE (Signal.pure 1#1) (Signal.pure 0#1)
  
  -- Extract bits [8:1] from shift register (the 8 data bits)
  let outByte8 := Signal.map (fun sr => BitVec.extractLsb 8 1 sr) shiftReg
  
  bundle2 outByte8 done

#synthesizeVerilog prob146_fsm_serialdata
