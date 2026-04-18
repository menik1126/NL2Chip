import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding (2 bits for 4 states)
private abbrev stWL : BitVec 2 := 0#2     -- Walking Left
private abbrev stWR : BitVec 2 := 1#2     -- Walking Right
private abbrev stFALLL : BitVec 2 := 2#2  -- Falling (was left)
private abbrev stFALLR : BitVec 2 := 3#2  -- Falling (was right)

/-- Lemmings2 FSM: 4 states (WL, WR, FALLL, FALLR).
    Walks left/right, switches on bump, falls when ground=0.
    Returns (walk_left, walk_right, aaah) as bundled signals. -/
def prob142_lemmings2 {dom : DomainConfig}
    (areset : Signal dom Bool)
    (bump_left bump_right : Signal dom Bool)
    (ground : Signal dom Bool)
    : Signal dom (BitVec 1 × BitVec 1 × BitVec 1) :=
  -- State register via Signal.loop
  let state : Signal dom (BitVec 2) :=
    Signal.loop fun (state : Signal dom (BitVec 2)) =>
      let isWL := state === (Signal.pure stWL)
      let isWR := state === (Signal.pure stWR)
      let isFALLL := state === (Signal.pure stFALLL)
      
      -- Next state logic based on current state
      -- WL: ground ? (bump_left ? WR : WL) : FALLL
      let nextFromWL := Signal.mux ground
        (Signal.mux bump_left (Signal.pure stWR) (Signal.pure stWL))
        (Signal.pure stFALLL)
      
      -- WR: ground ? (bump_right ? WL : WR) : FALLR
      let nextFromWR := Signal.mux ground
        (Signal.mux bump_right (Signal.pure stWL) (Signal.pure stWR))
        (Signal.pure stFALLR)
      
      -- FALLL: ground ? WL : FALLL
      let nextFromFALLL := Signal.mux ground (Signal.pure stWL) (Signal.pure stFALLL)
      
      -- FALLR: ground ? WR : FALLR
      let nextFromFALLR := Signal.mux ground (Signal.pure stWR) (Signal.pure stFALLR)
      
      -- Mux based on current state
      let nextState := 
        Signal.mux isWL nextFromWL
          (Signal.mux isWR nextFromWR
            (Signal.mux isFALLL nextFromFALLL nextFromFALLR))
      
      -- Apply async reset: areset → WL
      let nextWithReset := Signal.mux areset (Signal.pure stWL) nextState
      
      -- Register with initial value WL
      Signal.register stWL nextWithReset
  
  -- Derive outputs from state
  -- walk_left = (state == WL)
  let walk_left := Signal.mux (state === Signal.pure stWL) (Signal.pure 1#1) (Signal.pure 0#1)
  
  -- walk_right = (state == WR)
  let walk_right := Signal.mux (state === Signal.pure stWR) (Signal.pure 1#1) (Signal.pure 0#1)
  
  -- aaah = (state == FALLL || state == FALLR)
  let isFalling := (state === Signal.pure stFALLL) ||| (state === Signal.pure stFALLR)
  let aaah := Signal.mux isFalling (Signal.pure 1#1) (Signal.pure 0#1)
  
  bundle2 walk_left (bundle2 walk_right aaah)

#synthesizeVerilog prob142_lemmings2
