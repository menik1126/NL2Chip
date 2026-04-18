import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding (4 bits for 10 states)
private abbrev stS    : BitVec 4 := 0#4
private abbrev stS1   : BitVec 4 := 1#4
private abbrev stS11  : BitVec 4 := 2#4
private abbrev stS110 : BitVec 4 := 3#4
private abbrev stB0   : BitVec 4 := 4#4
private abbrev stB1   : BitVec 4 := 5#4
private abbrev stB2   : BitVec 4 := 6#4
private abbrev stB3   : BitVec 4 := 7#4
private abbrev stCount : BitVec 4 := 8#4
private abbrev stWait : BitVec 4 := 9#4

/-- FSM-based timer controller that detects pattern 1101, shifts 4 bits, counts, and waits for ack -/
def prob151_review2015_fsm {dom : DomainConfig}
    (reset : Signal dom Bool)
    (data : Signal dom Bool)
    (done_counting : Signal dom Bool)
    (ack : Signal dom Bool)
    : Signal dom ((BitVec 1 × BitVec 1) × BitVec 1) :=
  -- State register with feedback
  let state := Signal.loop fun s =>
    -- Next state logic using nested mux
    let isS := s === Signal.pure stS
    let isS1 := s === Signal.pure stS1
    let isS11 := s === Signal.pure stS11
    let isS110 := s === Signal.pure stS110
    let isB0 := s === Signal.pure stB0
    let isB1 := s === Signal.pure stB1
    let isB2 := s === Signal.pure stB2
    let isB3 := s === Signal.pure stB3
    let isCount := s === Signal.pure stCount
    let isWait := s === Signal.pure stWait
    
    let next := Signal.mux isS
      (Signal.mux data (Signal.pure stS1) (Signal.pure stS))
      (Signal.mux isS1
        (Signal.mux data (Signal.pure stS11) (Signal.pure stS))
        (Signal.mux isS11
          (Signal.mux data (Signal.pure stS11) (Signal.pure stS110))
          (Signal.mux isS110
            (Signal.mux data (Signal.pure stB0) (Signal.pure stS))
            (Signal.mux isB0
              (Signal.pure stB1)
              (Signal.mux isB1
                (Signal.pure stB2)
                (Signal.mux isB2
                  (Signal.pure stB3)
                  (Signal.mux isB3
                    (Signal.pure stCount)
                    (Signal.mux isCount
                      (Signal.mux done_counting (Signal.pure stWait) (Signal.pure stCount))
                      (Signal.mux isWait
                        (Signal.mux ack (Signal.pure stS) (Signal.pure stWait))
                        (Signal.pure stS))))))))))
    
    -- Apply reset
    let nextWithReset := Signal.mux reset (Signal.pure stS) next
    
    -- Register state
    Signal.register stS nextWithReset
  
  -- Output logic based on current state
  let shift_ena := 
    (state === Signal.pure stB0) |||
    (state === Signal.pure stB1) |||
    (state === Signal.pure stB2) |||
    (state === Signal.pure stB3)
  
  let counting := state === Signal.pure stCount
  
  let done := state === Signal.pure stWait
  
  -- Convert Bool to BitVec 1
  let shift_ena_bv := Signal.mux shift_ena (Signal.pure 1#1) (Signal.pure 0#1)
  let counting_bv := Signal.mux counting (Signal.pure 1#1) (Signal.pure 0#1)
  let done_bv := Signal.mux done (Signal.pure 1#1) (Signal.pure 0#1)
  
  bundle2 (bundle2 shift_ena_bv counting_bv) done_bv

#synthesizeVerilog prob151_review2015_fsm
