import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding (4 bits for 12 states)
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

/-- Serial protocol FSM: detects start bit (0), 8 data bits, stop bit (1).
    Outputs done=1 for one cycle when byte correctly received. -/
def prob137_fsm_serial {dom : DomainConfig}
    (reset : Signal dom Bool)
    (inp : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  -- State register via Signal.loop
  let state : Signal dom (BitVec 4) :=
    Signal.loop fun (state : Signal dom (BitVec 4)) =>
      -- Next state logic based on current state
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
      
      -- Next state transitions
      let nextFromSTART := Signal.mux inp (Signal.pure stSTART) (Signal.pure stB0)
      let nextFromB0    := Signal.pure stB1
      let nextFromB1    := Signal.pure stB2
      let nextFromB2    := Signal.pure stB3
      let nextFromB3    := Signal.pure stB4
      let nextFromB4    := Signal.pure stB5
      let nextFromB5    := Signal.pure stB6
      let nextFromB6    := Signal.pure stB7
      let nextFromB7    := Signal.pure stSTOP
      let nextFromSTOP  := Signal.mux inp (Signal.pure stDONE) (Signal.pure stERR)
      let nextFromDONE  := Signal.mux inp (Signal.pure stSTART) (Signal.pure stB0)
      let nextFromERR   := Signal.mux inp (Signal.pure stSTART) (Signal.pure stERR)
      
      -- Mux chain to select next state
      let nextState := 
        hw_cond (Signal.pure stSTART)
          | isSTART => nextFromSTART
          | isB0    => nextFromB0
          | isB1    => nextFromB1
          | isB2    => nextFromB2
          | isB3    => nextFromB3
          | isB4    => nextFromB4
          | isB5    => nextFromB5
          | isB6    => nextFromB6
          | isB7    => nextFromB7
          | isSTOP  => nextFromSTOP
          | isDONE  => nextFromDONE
          | isERR   => nextFromERR
      
      -- Apply synchronous reset: reset → START
      let nextWithReset := Signal.mux reset (Signal.pure stSTART) nextState
      
      -- Register with initial value START
      Signal.register stSTART nextWithReset
  
  -- Output: done = (state == DONE)
  let isDONE := state === Signal.pure stDONE
  Signal.mux isDONE (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob137_fsm_serial
