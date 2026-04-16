import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding (4 bits)
-- B0=0, B1=1, B2=2, B3=3, B4=4, B5=5, B6=6, B7=7, START=8, STOP=9, DONE=10, ERR=11
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

/-- Serial protocol FSM: detects correctly framed bytes (start=0, 8 data bits, stop=1).
    Asserts done for one cycle after a valid byte is received.
    Synchronous active-high reset returns to START state. -/
def prob137_fsm_serial {dom : DomainConfig}
    (reset : Signal dom Bool)
    (inp   : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  -- Compute state register using Signal.loop
  let state : Signal dom (BitVec 4) :=
    Signal.loop fun (state : Signal dom (BitVec 4)) =>
      -- Decode current state
      let isSTART := state === (Signal.pure stSTART)
      let isB0    := state === (Signal.pure stB0)
      let isB1    := state === (Signal.pure stB1)
      let isB2    := state === (Signal.pure stB2)
      let isB3    := state === (Signal.pure stB3)
      let isB4    := state === (Signal.pure stB4)
      let isB5    := state === (Signal.pure stB5)
      let isB6    := state === (Signal.pure stB6)
      let isB7    := state === (Signal.pure stB7)
      let isSTOP  := state === (Signal.pure stSTOP)
      let isDONE  := state === (Signal.pure stDONE)
      -- ERR state: none of the above
      let isERR   := ~~~(isSTART ||| isB0 ||| isB1 ||| isB2 ||| isB3 |||
                         isB4    ||| isB5 ||| isB6 ||| isB7 ||| isSTOP ||| isDONE)

      -- Next state logic using hw_cond (priority mux)
      -- START: in=1 → START (idle), in=0 → B0 (start bit detected)
      -- B0→B6: always advance to next bit
      -- B7: go to STOP
      -- STOP: in=1 → DONE, in=0 → ERR
      -- DONE: in=1 → START, in=0 → B0
      -- ERR:  in=1 → START, in=0 → ERR
      let nextFromSTART := Signal.mux inp (Signal.pure stSTART) (Signal.pure stB0)
      let nextFromSTOP  := Signal.mux inp (Signal.pure stDONE)  (Signal.pure stERR)
      let nextFromDONE  := Signal.mux inp (Signal.pure stSTART) (Signal.pure stB0)
      let nextFromERR   := Signal.mux inp (Signal.pure stSTART) (Signal.pure stERR)

      let nextState :=
        hw_cond (Signal.pure stSTART)
        | isSTART => nextFromSTART
        | isB0    => Signal.pure stB1
        | isB1    => Signal.pure stB2
        | isB2    => Signal.pure stB3
        | isB3    => Signal.pure stB4
        | isB4    => Signal.pure stB5
        | isB5    => Signal.pure stB6
        | isB6    => Signal.pure stB7
        | isB7    => Signal.pure stSTOP
        | isSTOP  => nextFromSTOP
        | isDONE  => nextFromDONE
        | isERR   => nextFromERR

      -- Apply synchronous reset
      let nextWithReset := Signal.mux reset (Signal.pure stSTART) nextState

      -- Register with initial value START
      Signal.register stSTART nextWithReset

  -- Output: done = (state == DONE)
  Signal.mux (state === (Signal.pure stDONE)) (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob137_fsm_serial
