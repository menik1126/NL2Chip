import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding
private abbrev stWL : BitVec 1 := 0#1  -- Walking Left
private abbrev stWR : BitVec 1 := 1#1  -- Walking Right

/-- Lemmings FSM: 2 states (WalkLeft=0, WalkRight=1).
    Switches direction on bump. Async reset to WalkLeft.
    Returns (walk_left, walk_right) as bundled BitVec 1 signals. -/
def prob127_lemmings1 {dom : DomainConfig}
    (areset : Signal dom Bool)
    (bump_left bump_right : Signal dom Bool)
    : Signal dom (BitVec 1 × BitVec 1) :=
  -- State register via Signal.loop
  let state : Signal dom (BitVec 1) :=
    Signal.loop fun (state : Signal dom (BitVec 1)) =>
      let isWL := state === (Signal.pure stWL)
      -- Next state logic:
      --   WL + bump_left  → WR,  WL + no bump → WL
      --   WR + bump_right → WL,  WR + no bump → WR
      -- If bumped on both sides, still switch.
      let nextFromWL := Signal.mux bump_left  (Signal.pure stWR) (Signal.pure stWL)
      let nextFromWR := Signal.mux bump_right (Signal.pure stWL) (Signal.pure stWR)
      let nextState := Signal.mux isWL nextFromWL nextFromWR
      -- Apply async reset (modeled as sync): areset → WL
      let nextWithReset := Signal.mux areset (Signal.pure stWL) nextState
      -- Register with initial value WL
      Signal.register stWL nextWithReset
  -- Derive outputs from state
  -- walk_left = (state == WL), i.e., ~state since WL=0
  -- walk_right = state since WR=1
  let walk_left  := ~~~state
  let walk_right := state
  bundle2 walk_left walk_right

#synthesizeVerilog prob127_lemmings1
