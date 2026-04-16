import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding (6 states need 3 bits)
private abbrev stWL    : BitVec 3 := 0#3  -- Walking Left
private abbrev stWR    : BitVec 3 := 1#3  -- Walking Right
private abbrev stFALLL : BitVec 3 := 2#3  -- Falling (was walking left)
private abbrev stFALLR : BitVec 3 := 3#3  -- Falling (was walking right)
private abbrev stDIGL  : BitVec 3 := 4#3  -- Digging (was walking left)
private abbrev stDIGR  : BitVec 3 := 5#3  -- Digging (was walking right)

/-- Lemmings FSM with falling and digging: 6 states (WL, WR, FALLL, FALLR, DIGL, DIGR).
    Switches direction on bump while walking. Falls when ground=0 (highest priority).
    Digs when dig=1 while walking (second priority). Bumps have lowest priority.
    Async reset to WalkLeft.
    Returns (walk_left, walk_right, aaah, digging) as bundled signals. -/
def prob152_lemmings3 {dom : DomainConfig}
    (areset : Signal dom Bool)
    (bump_left bump_right ground dig : Signal dom Bool)
    : Signal dom (BitVec 1 × BitVec 1 × BitVec 1 × BitVec 1) :=
  -- State register via Signal.loop
  let state : Signal dom (BitVec 3) :=
    Signal.loop fun (state : Signal dom (BitVec 3)) =>
      let isWL    := state === (Signal.pure stWL)
      let isWR    := state === (Signal.pure stWR)
      let isFALLL := state === (Signal.pure stFALLL)
      let isFALLR := state === (Signal.pure stFALLR)
      let isDIGL  := state === (Signal.pure stDIGL)
      -- isDIGR is the default (else) case

      -- Next state logic for each current state:
      -- WL: ground=0 → FALLL (highest priority)
      --     else dig=1 → DIGL
      --     else bump_left=1 → WR
      --     else → WL
      let nextFromWL := Signal.mux ground
        (Signal.mux dig (Signal.pure stDIGL)
          (Signal.mux bump_left (Signal.pure stWR) (Signal.pure stWL)))
        (Signal.pure stFALLL)

      -- WR: ground=0 → FALLR (highest priority)
      --     else dig=1 → DIGR
      --     else bump_right=1 → WL
      --     else → WR
      let nextFromWR := Signal.mux ground
        (Signal.mux dig (Signal.pure stDIGR)
          (Signal.mux bump_right (Signal.pure stWL) (Signal.pure stWR)))
        (Signal.pure stFALLR)

      -- FALLL: ground=1 → WL, else FALLL (bumps ignored)
      let nextFromFALLL := Signal.mux ground (Signal.pure stWL) (Signal.pure stFALLL)

      -- FALLR: ground=1 → WR, else FALLR (bumps ignored)
      let nextFromFALLR := Signal.mux ground (Signal.pure stWR) (Signal.pure stFALLR)

      -- DIGL: ground=1 → DIGL (continue digging), ground=0 → FALLL
      let nextFromDIGL := Signal.mux ground (Signal.pure stDIGL) (Signal.pure stFALLL)

      -- DIGR: ground=1 → DIGR (continue digging), ground=0 → FALLR
      let nextFromDIGR := Signal.mux ground (Signal.pure stDIGR) (Signal.pure stFALLR)

      -- Select next state based on current state
      let nextState := Signal.mux isWL nextFromWL
        (Signal.mux isWR nextFromWR
          (Signal.mux isFALLL nextFromFALLL
            (Signal.mux isFALLR nextFromFALLR
              (Signal.mux isDIGL nextFromDIGL nextFromDIGR))))

      -- Apply async reset (modeled as sync): areset → WL
      let nextWithReset := Signal.mux areset (Signal.pure stWL) nextState

      -- Register with initial value WL
      Signal.register stWL nextWithReset

  -- Derive outputs from state
  let walk_left  := Signal.mux (state === (Signal.pure stWL))    (Signal.pure 1#1) (Signal.pure 0#1)
  let walk_right := Signal.mux (state === (Signal.pure stWR))    (Signal.pure 1#1) (Signal.pure 0#1)
  let aaah       := Signal.mux ((state === (Signal.pure stFALLL)) ||| (state === (Signal.pure stFALLR)))
    (Signal.pure 1#1) (Signal.pure 0#1)
  let digging    := Signal.mux ((state === (Signal.pure stDIGL)) ||| (state === (Signal.pure stDIGR)))
    (Signal.pure 1#1) (Signal.pure 0#1)

  bundle2 walk_left (bundle2 walk_right (bundle2 aaah digging))

#synthesizeVerilog prob152_lemmings3
