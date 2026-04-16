import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding (4 bits to represent 12 states)
private abbrev stSTART : BitVec 4 := 8#4
private abbrev stB0    : BitVec 4 := 0#4
private abbrev stB1    : BitVec 4 := 1#4
private abbrev stB2    : BitVec 4 := 2#4
private abbrev stB3    : BitVec 4 := 3#4
private abbrev stB4    : BitVec 4 := 4#4
private abbrev stB5    : BitVec 4 := 5#4
private abbrev stB6    : BitVec 4 := 6#4
private abbrev stB7    : BitVec 4 := 7#4
private abbrev stSTOP  : BitVec 4 := 9#4
private abbrev stDONE  : BitVec 4 := 10#4
private abbrev stERR   : BitVec 4 := 11#4

/-- Serial data receiver FSM: receives bytes with start bit (0), 8 data bits (LSB first),
    and stop bit (1). Outputs the received byte when done is asserted.
    Uses a 10-bit shift register to capture the serial stream.
    At DONE state, bits [8:1] of the shift register contain the 8 data bits. -/
def prob146_fsm_serialdata {dom : DomainConfig}
    (inp : Signal dom Bool)
    (reset : Signal dom Bool)
    : Signal dom (BitVec 8 × BitVec 1) :=
  -- FSM state register
  let state : Signal dom (BitVec 4) :=
    Signal.loop fun (s : Signal dom (BitVec 4)) =>
      -- State comparisons
      let isSTART := s === Signal.pure stSTART
      let isB0    := s === Signal.pure stB0
      let isB1    := s === Signal.pure stB1
      let isB2    := s === Signal.pure stB2
      let isB3    := s === Signal.pure stB3
      let isB4    := s === Signal.pure stB4
      let isB5    := s === Signal.pure stB5
      let isB6    := s === Signal.pure stB6
      let isB7    := s === Signal.pure stB7
      let isSTOP  := s === Signal.pure stSTOP
      let isDONE  := s === Signal.pure stDONE
      let isERR   := s === Signal.pure stERR
      -- Next state per-state computations
      -- START: stay if in=1 (idle), go to B0 if in=0 (start bit detected)
      let nSTART  := Signal.mux inp (Signal.pure stSTART) (Signal.pure stB0)
      -- STOP: go DONE if in=1 (stop bit ok), go ERR if in=0 (framing error)
      let nSTOP   := Signal.mux inp (Signal.pure stDONE) (Signal.pure stERR)
      -- DONE: if in=1 go START (idle), if in=0 go B0 (new start bit)
      let nDONE   := Signal.mux inp (Signal.pure stSTART) (Signal.pure stB0)
      -- ERR: wait for in=1 (stop/idle), then go START; else stay ERR
      let nERR    := Signal.mux inp (Signal.pure stSTART) (Signal.pure stERR)
      -- Priority mux chain: isSTART first, then each bit state, then STOP/DONE/ERR
      let nextState :=
        Signal.mux isSTART nSTART
          (Signal.mux isB0 (Signal.pure stB1)
            (Signal.mux isB1 (Signal.pure stB2)
              (Signal.mux isB2 (Signal.pure stB3)
                (Signal.mux isB3 (Signal.pure stB4)
                  (Signal.mux isB4 (Signal.pure stB5)
                    (Signal.mux isB5 (Signal.pure stB6)
                      (Signal.mux isB6 (Signal.pure stB7)
                        (Signal.mux isB7 (Signal.pure stSTOP)
                          (Signal.mux isSTOP nSTOP
                            (Signal.mux isDONE nDONE
                              (Signal.mux isERR nERR
                                (Signal.pure stSTART))))))))))))
      -- Apply synchronous reset: reset → START
      let nextWithReset := Signal.mux reset (Signal.pure stSTART) nextState
      Signal.register stSTART nextWithReset

  -- 10-bit shift register: shifts right every clock, capturing serial stream
  -- Reference: byte_r <= {in, byte_r[9:1]}
  -- At DONE state: byte_r[8:1] = the received byte (8 data bits)
  let byteReg : Signal dom (BitVec 10) :=
    Signal.loop fun (br : Signal dom (BitVec 10)) =>
      -- Shift right by 1, insert 'in' at MSB (bit 9)
      -- 512 = 2^9 = bit 9 set
      let shifted : Signal dom (BitVec 10) := br >>> 1#10
      let inBit : Signal dom (BitVec 10) :=
        Signal.mux inp (Signal.pure 512#10) (Signal.pure 0#10)
      let nextReg := shifted ||| inBit
      Signal.register 0#10 nextReg

  -- Output logic
  let isDONE := state === Signal.pure stDONE
  -- Extract bits [8:1] from the 10-bit shift register as the output byte
  let outByte : Signal dom (BitVec 8) :=
    Signal.map (fun br => BitVec.extractLsb' 1 8 br) byteReg
  -- done flag as 1-bit signal
  let doneBit : Signal dom (BitVec 1) :=
    Signal.mux isDONE (Signal.pure 1#1) (Signal.pure 0#1)

  bundle2 outByte doneBit

#synthesizeVerilog prob146_fsm_serialdata
