import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding (6 states need 3 bits)
private abbrev stWL : BitVec 3 := 0#3      -- Walk Left
private abbrev stWR : BitVec 3 := 1#3      -- Walk Right
private abbrev stFALLL : BitVec 3 := 2#3   -- Fall Left
private abbrev stFALLR : BitVec 3 := 3#3   -- Fall Right
private abbrev stDIGL : BitVec 3 := 4#3    -- Dig Left
private abbrev stDIGR : BitVec 3 := 5#3    -- Dig Right

/-- Lemmings FSM: models walking, falling, and digging behavior -/
def prob152_lemmings3 {dom : DomainConfig}
    (areset : Signal dom Bool)
    (bump_left : Signal dom Bool)
    (bump_right : Signal dom Bool)
    (ground : Signal dom Bool)
    (dig : Signal dom Bool)
    : Signal dom ((BitVec 1 × BitVec 1) × (BitVec 1 × BitVec 1)) :=
  let stateSignal := Signal.loop fun (state : Signal dom (BitVec 3)) =>
    -- Next state logic based on current state
    let isWL := state === Signal.pure stWL
    let isWR := state === Signal.pure stWR
    let isFALLL := state === Signal.pure stFALLL
    let isFALLR := state === Signal.pure stFALLR
    let isDIGL := state === Signal.pure stDIGL
    let isDIGR := state === Signal.pure stDIGR
    
    let notGround := ~~~ground
    
    -- Next state for WL: priority is !ground > dig > bump_left
    let nextWL := 
      Signal.mux notGround (Signal.pure stFALLL)
        (Signal.mux dig (Signal.pure stDIGL)
          (Signal.mux bump_left (Signal.pure stWR) (Signal.pure stWL)))
    
    -- Next state for WR: priority is !ground > dig > bump_right
    let nextWR := 
      Signal.mux notGround (Signal.pure stFALLR)
        (Signal.mux dig (Signal.pure stDIGR)
          (Signal.mux bump_right (Signal.pure stWL) (Signal.pure stWR)))
    
    -- Next state for FALLL: ground → WL, else stay FALLL
    let nextFALLL := Signal.mux ground (Signal.pure stWL) (Signal.pure stFALLL)
    
    -- Next state for FALLR: ground → WR, else stay FALLR
    let nextFALLR := Signal.mux ground (Signal.pure stWR) (Signal.pure stFALLR)
    
    -- Next state for DIGL: ground → stay DIGL, else FALLL
    let nextDIGL := Signal.mux ground (Signal.pure stDIGL) (Signal.pure stFALLL)
    
    -- Next state for DIGR: ground → stay DIGR, else FALLR
    let nextDIGR := Signal.mux ground (Signal.pure stDIGR) (Signal.pure stFALLR)
    
    -- Mux all next states based on current state
    let nextState := 
      Signal.mux isWL nextWL
        (Signal.mux isWR nextWR
          (Signal.mux isFALLL nextFALLL
            (Signal.mux isFALLR nextFALLR
              (Signal.mux isDIGL nextDIGL nextDIGR))))
    
    -- Apply async reset (modeled as sync mux)
    let nextWithReset := Signal.mux areset (Signal.pure stWL) nextState
    
    -- Register with initial value WL
    Signal.register stWL nextWithReset
  
  -- Moore outputs based on registered state
  -- Convert state comparisons to BitVec outputs
  let isWL := stateSignal === Signal.pure stWL
  let isWR := stateSignal === Signal.pure stWR
  let isFALLL := stateSignal === Signal.pure stFALLL
  let isFALLR := stateSignal === Signal.pure stFALLR
  let isDIGL := stateSignal === Signal.pure stDIGL
  let isDIGR := stateSignal === Signal.pure stDIGR
  
  let walk_left := Signal.mux isWL (Signal.pure 1#1) (Signal.pure 0#1)
  let walk_right := Signal.mux isWR (Signal.pure 1#1) (Signal.pure 0#1)
  let aaah := Signal.mux (isFALLL ||| isFALLR) (Signal.pure 1#1) (Signal.pure 0#1)
  let digging := Signal.mux (isDIGL ||| isDIGR) (Signal.pure 1#1) (Signal.pure 0#1)
  
  -- Bundle 4 outputs as nested pairs: ((walk_left, walk_right), (aaah, digging))
  let pair1 := bundle2 walk_left walk_right
  let pair2 := bundle2 aaah digging
  bundle2 pair1 pair2

#synthesizeVerilog prob152_lemmings3
