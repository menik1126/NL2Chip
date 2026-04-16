import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding (4-bit):
-- S0=0: initial / saw 0
-- S1=1: saw 1 consecutive 1
-- S2=2: saw 2 consecutive 1s
-- S3=3: saw 3 consecutive 1s
-- S4=4: saw 4 consecutive 1s
-- S5=5: saw 5 consecutive 1s
-- S6=6: saw 6 consecutive 1s
-- SERR=7: error (7+ consecutive 1s)
-- SDISC=8: discard state (after 5 ones and a zero)
-- SFLAG=9: flag state (after 6 ones and a zero)
private abbrev stS0    : BitVec 4 := 0#4
private abbrev stS1    : BitVec 4 := 1#4
private abbrev stS2    : BitVec 4 := 2#4
private abbrev stS3    : BitVec 4 := 3#4
private abbrev stS4    : BitVec 4 := 4#4
private abbrev stS5    : BitVec 4 := 5#4
private abbrev stS6    : BitVec 4 := 6#4
private abbrev stSERR  : BitVec 4 := 7#4
private abbrev stSDISC : BitVec 4 := 8#4
private abbrev stSFLAG : BitVec 4 := 9#4

/-- HDLC framing FSM: detects disc (5 ones then zero), flag (6 ones then zero),
    and err (7+ consecutive ones). Moore-type with synchronous reset.
    Returns bundled (disc, flag, err) as 1-bit signals. -/
def prob140_fsm_hdlc {dom : DomainConfig}
    (reset : Signal dom Bool)
    (inp : Signal dom Bool)
    : Signal dom (BitVec 1 × BitVec 1 × BitVec 1) :=
  -- State register via Signal.loop (loop body must return Signal dom (BitVec 4))
  let state : Signal dom (BitVec 4) :=
    Signal.loop fun (state : Signal dom (BitVec 4)) =>
      -- Per-state next-state computations
      let fromS0    := Signal.mux inp (Signal.pure stS1)    (Signal.pure stS0)
      let fromS1    := Signal.mux inp (Signal.pure stS2)    (Signal.pure stS0)
      let fromS2    := Signal.mux inp (Signal.pure stS3)    (Signal.pure stS0)
      let fromS3    := Signal.mux inp (Signal.pure stS4)    (Signal.pure stS0)
      let fromS4    := Signal.mux inp (Signal.pure stS5)    (Signal.pure stS0)
      let fromS5    := Signal.mux inp (Signal.pure stS6)    (Signal.pure stSDISC)
      let fromS6    := Signal.mux inp (Signal.pure stSERR)  (Signal.pure stSFLAG)
      let fromSERR  := Signal.mux inp (Signal.pure stSERR)  (Signal.pure stS0)
      let fromSFLAG := Signal.mux inp (Signal.pure stS1)    (Signal.pure stS0)
      let fromSDISC := Signal.mux inp (Signal.pure stS1)    (Signal.pure stS0)
      -- Select next state based on current state
      let nextState : Signal dom (BitVec 4) :=
        hw_cond fromS0
          | (state === Signal.pure stS1)    => fromS1
          | (state === Signal.pure stS2)    => fromS2
          | (state === Signal.pure stS3)    => fromS3
          | (state === Signal.pure stS4)    => fromS4
          | (state === Signal.pure stS5)    => fromS5
          | (state === Signal.pure stS6)    => fromS6
          | (state === Signal.pure stSERR)  => fromSERR
          | (state === Signal.pure stSFLAG) => fromSFLAG
          | (state === Signal.pure stSDISC) => fromSDISC
      -- Apply synchronous reset
      let nextWithReset := Signal.mux reset (Signal.pure stS0) nextState
      -- Register the state
      Signal.register stS0 nextWithReset
  -- Compute Moore outputs from state
  let disc : Signal dom (BitVec 1) :=
    Signal.mux (state === Signal.pure stSDISC) (Signal.pure 1#1) (Signal.pure 0#1)
  let flag : Signal dom (BitVec 1) :=
    Signal.mux (state === Signal.pure stSFLAG) (Signal.pure 1#1) (Signal.pure 0#1)
  let err : Signal dom (BitVec 1) :=
    Signal.mux (state === Signal.pure stSERR) (Signal.pure 1#1) (Signal.pure 0#1)
  bundle2 disc (bundle2 flag err)

#synthesizeVerilog prob140_fsm_hdlc
