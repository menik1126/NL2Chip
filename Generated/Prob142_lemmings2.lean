import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding (2-bit):
-- WL   = 0: Walking Left
-- WR   = 1: Walking Right
-- FALLL = 2: Falling (was walking Left)
-- FALLR = 3: Falling (was walking Right)
private abbrev stWL    : BitVec 2 := 0#2
private abbrev stWR    : BitVec 2 := 1#2
private abbrev stFALLL : BitVec 2 := 2#2
private abbrev stFALLR : BitVec 2 := 3#2

/-- Lemmings 2 FSM: 4 states (WL, WR, FALLL, FALLR).
    Switches direction when bumped while walking on ground.
    Falls (aaah=1) when ground=0, resumes direction when ground returns.
    Async reset to WL modeled as synchronous reset via mux.
    Returns (walk_left, walk_right, aaah) as bundled signals. -/
def prob142_lemmings2 {dom : DomainConfig}
    (areset : Signal dom Bool)
    (bump_left bump_right ground : Signal dom Bool)
    : Signal dom (BitVec 1 × BitVec 1 × BitVec 1) :=
  let state : Signal dom (BitVec 2) :=
    Signal.loop fun (state : Signal dom (BitVec 2)) =>
      -- Next state logic (matches reference Verilog):
      --   WL:    ground ? (bump_left  ? WR : WL) : FALLL
      --   WR:    ground ? (bump_right ? WL : WR) : FALLR
      --   FALLL: ground ? WL : FALLL
      --   FALLR: ground ? WR : FALLR

      -- From WL
      let fromWL := Signal.mux ground
        (Signal.mux bump_left (Signal.pure stWR) (Signal.pure stWL))
        (Signal.pure stFALLL)
      -- From WR
      let fromWR := Signal.mux ground
        (Signal.mux bump_right (Signal.pure stWL) (Signal.pure stWR))
        (Signal.pure stFALLR)
      -- From FALLL
      let fromFALLL := Signal.mux ground (Signal.pure stWL) (Signal.pure stFALLL)
      -- From FALLR
      let fromFALLR := Signal.mux ground (Signal.pure stWR) (Signal.pure stFALLR)

      -- Select next state based on current state
      let nextState := hw_cond fromWL
        | (state === Signal.pure stWR)    => fromWR
        | (state === Signal.pure stFALLL) => fromFALLL
        | (state === Signal.pure stFALLR) => fromFALLR

      -- Apply async reset (modeled as sync): areset → WL
      let nextWithReset := Signal.mux areset (Signal.pure stWL) nextState

      -- Register state with initial value WL
      Signal.register stWL nextWithReset

  -- Derive outputs from state (Moore outputs)
  let walk_left  : Signal dom (BitVec 1) :=
    Signal.mux (state === Signal.pure stWL) (Signal.pure 1#1) (Signal.pure 0#1)
  let walk_right : Signal dom (BitVec 1) :=
    Signal.mux (state === Signal.pure stWR) (Signal.pure 1#1) (Signal.pure 0#1)
  let isfall := (state === Signal.pure stFALLL) ||| (state === Signal.pure stFALLR)
  let aaah : Signal dom (BitVec 1) :=
    Signal.mux isfall (Signal.pure 1#1) (Signal.pure 0#1)

  bundle2 walk_left (bundle2 walk_right aaah)

#synthesizeVerilog prob142_lemmings2
