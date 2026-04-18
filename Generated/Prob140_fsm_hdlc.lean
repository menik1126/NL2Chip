import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding (4 bits to match reference)
private abbrev S0    : BitVec 4 := 0#4
private abbrev S1    : BitVec 4 := 1#4
private abbrev S2    : BitVec 4 := 2#4
private abbrev S3    : BitVec 4 := 3#4
private abbrev S4    : BitVec 4 := 4#4
private abbrev S5    : BitVec 4 := 5#4
private abbrev S6    : BitVec 4 := 6#4
private abbrev SERR  : BitVec 4 := 7#4
private abbrev SDISC : BitVec 4 := 8#4
private abbrev SFLAG : BitVec 4 := 9#4

/-- HDLC framing FSM: detects flag (01111110), discard (0111110), and error (7+ ones) -/
def prob140_fsm_hdlc {dom : DomainConfig}
    (reset : Signal dom Bool)
    (inp : Signal dom Bool)
    : Signal dom (BitVec 1 × BitVec 1 × BitVec 1) :=
  let state := Signal.loop fun (state : Signal dom (BitVec 4)) =>
    -- Check current state
    let isS0 := state === Signal.pure S0
    let isS1 := state === Signal.pure S1
    let isS2 := state === Signal.pure S2
    let isS3 := state === Signal.pure S3
    let isS4 := state === Signal.pure S4
    let isS5 := state === Signal.pure S5
    let isS6 := state === Signal.pure S6
    let isSERR := state === Signal.pure SERR
    let isSFLAG := state === Signal.pure SFLAG
    let isSDisc := state === Signal.pure SDISC
    
    -- Compute next state for each current state
    let nextFromS0 := Signal.mux inp (Signal.pure S1) (Signal.pure S0)
    let nextFromS1 := Signal.mux inp (Signal.pure S2) (Signal.pure S0)
    let nextFromS2 := Signal.mux inp (Signal.pure S3) (Signal.pure S0)
    let nextFromS3 := Signal.mux inp (Signal.pure S4) (Signal.pure S0)
    let nextFromS4 := Signal.mux inp (Signal.pure S5) (Signal.pure S0)
    let nextFromS5 := Signal.mux inp (Signal.pure S6) (Signal.pure SDISC)
    let nextFromS6 := Signal.mux inp (Signal.pure SERR) (Signal.pure SFLAG)
    let nextFromSERR := Signal.mux inp (Signal.pure SERR) (Signal.pure S0)
    let nextFromSFLAG := Signal.mux inp (Signal.pure S1) (Signal.pure S0)
    let nextFromSDisc := Signal.mux inp (Signal.pure S1) (Signal.pure S0)
    
    -- Select next state based on current state (priority mux)
    let nextState := 
      Signal.mux isS0 nextFromS0
        (Signal.mux isS1 nextFromS1
          (Signal.mux isS2 nextFromS2
            (Signal.mux isS3 nextFromS3
              (Signal.mux isS4 nextFromS4
                (Signal.mux isS5 nextFromS5
                  (Signal.mux isS6 nextFromS6
                    (Signal.mux isSERR nextFromSERR
                      (Signal.mux isSFLAG nextFromSFLAG
                        (Signal.mux isSDisc nextFromSDisc (Signal.pure S0))))))))))
    
    -- Apply reset
    let nextWithReset := Signal.mux reset (Signal.pure S0) nextState
    
    -- Register state
    Signal.register S0 nextWithReset
  
  -- Moore outputs based on current state
  let disc := state === Signal.pure SDISC
  let flag := state === Signal.pure SFLAG
  let err := state === Signal.pure SERR
  
  -- Convert Bool to BitVec 1 for outputs
  let discOut := Signal.mux disc (Signal.pure 1#1) (Signal.pure 0#1)
  let flagOut := Signal.mux flag (Signal.pure 1#1) (Signal.pure 0#1)
  let errOut := Signal.mux err (Signal.pure 1#1) (Signal.pure 0#1)
  
  bundle2 discOut (bundle2 flagOut errOut)

#synthesizeVerilog prob140_fsm_hdlc
