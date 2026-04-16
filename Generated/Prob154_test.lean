import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding (3 bits for 7 states)
private abbrev stWL    : BitVec 3 := 0#3  -- Walk Left
private abbrev stWR    : BitVec 3 := 1#3  -- Walk Right
private abbrev stFALLL : BitVec 3 := 2#3  -- Fall Left
private abbrev stFALLR : BitVec 3 := 3#3  -- Fall Right
private abbrev stDIGL  : BitVec 3 := 4#3  -- Dig Left
private abbrev stDIGR  : BitVec 3 := 5#3  -- Dig Right
private abbrev stDEAD  : BitVec 3 := 6#3  -- Dead (splattered)

/-- Lemmings FSM with walking, falling, digging, and splatting.
    Returns (walk_left, walk_right, aaah, digging) as bundled signals. -/
def prob155_lemmings4 {dom : DomainConfig}
    (areset : Signal dom Bool)
    (bump_left bump_right : Signal dom Bool)
    (ground : Signal dom Bool)
    (dig : Signal dom Bool)
    : Signal dom (BitVec 1 × BitVec 1 × BitVec 1 × BitVec 1) :=
  -- Combined state and fall counter using Signal.loop
  let stateAndCounter : Signal dom (BitVec 3 × BitVec 5) :=
    Signal.loop fun (sc : Signal dom (BitVec 3 × BitVec 5)) =>
      let state := Signal.map Prod.fst sc
      let fall_counter := Signal.map Prod.snd sc
      
      -- State comparisons
      let isWL    := state === Signal.pure stWL
      let isWR    := state === Signal.pure stWR
      let isFALLL := state === Signal.pure stFALLL
      let isFALLR := state === Signal.pure stFALLR
      let isDIGL  := state === Signal.pure stDIGL
      let isDIGR  := state === Signal.pure stDIGR
      let isDEAD  := state === Signal.pure stDEAD
      
      -- Fall counter check: counter >= 20 is !(counter < 20)
      let counterGTE20 := ~~~(Signal.ultC fall_counter 20#5)
      
      -- Next state logic (priority: fall > dig > bump)
      -- WL: if !ground then FALLL, else if dig then DIGL, else if bump_left then WR, else WL
      let nextFromWL := 
        Signal.mux (~~~ground) (Signal.pure stFALLL)
          (Signal.mux dig (Signal.pure stDIGL)
            (Signal.mux bump_left (Signal.pure stWR) (Signal.pure stWL)))
      
      -- WR: if !ground then FALLR, else if dig then DIGR, else if bump_right then WL, else WR
      let nextFromWR := 
        Signal.mux (~~~ground) (Signal.pure stFALLR)
          (Signal.mux dig (Signal.pure stDIGR)
            (Signal.mux bump_right (Signal.pure stWL) (Signal.pure stWR)))
      
      -- FALLL: if ground then (counter >= 20 ? DEAD : WL) else FALLL
      let nextFromFALLL := 
        Signal.mux ground 
          (Signal.mux counterGTE20 (Signal.pure stDEAD) (Signal.pure stWL))
          (Signal.pure stFALLL)
      
      -- FALLR: if ground then (counter >= 20 ? DEAD : WR) else FALLR
      let nextFromFALLR := 
        Signal.mux ground 
          (Signal.mux counterGTE20 (Signal.pure stDEAD) (Signal.pure stWR))
          (Signal.pure stFALLR)
      
      -- DIGL: if ground then DIGL else FALLL
      let nextFromDIGL := Signal.mux ground (Signal.pure stDIGL) (Signal.pure stFALLL)
      
      -- DIGR: if ground then DIGR else FALLR
      let nextFromDIGR := Signal.mux ground (Signal.pure stDIGR) (Signal.pure stFALLR)
      
      -- DEAD: stay DEAD
      let nextFromDEAD := Signal.pure stDEAD
      
      -- Mux chain for next state
      let nextState := 
        Signal.mux isWL nextFromWL
          (Signal.mux isWR nextFromWR
            (Signal.mux isFALLL nextFromFALLL
              (Signal.mux isFALLR nextFromFALLR
                (Signal.mux isDIGL nextFromDIGL
                  (Signal.mux isDIGR nextFromDIGR nextFromDEAD)))))
      
      -- Apply async reset (modeled as sync)
      let nextStateWithReset := Signal.mux areset (Signal.pure stWL) nextState
      
      -- Fall counter logic
      -- If in FALLL or FALLR state, increment (up to 20), else reset to 0
      let isFalling := isFALLL ||| isFALLR
      let counterLT20 := Signal.ultC fall_counter 20#5
      let shouldIncrement := isFalling &&& counterLT20
      let nextCounter := Signal.mux isFalling
        (Signal.mux shouldIncrement (fall_counter + 1#5) fall_counter)
        (Signal.pure 0#5)
      
      -- Register both state and counter
      let regState := Signal.register stWL nextStateWithReset
      let regCounter := Signal.register 0#5 nextCounter
      
      bundle2 regState regCounter
  
  -- Extract state from the loop result
  let state := Signal.map Prod.fst stateAndCounter
  
  -- Derive outputs from state (convert Bool comparisons to BitVec 1)
  let isWL := state === Signal.pure stWL
  let isWR := state === Signal.pure stWR
  let isFALLL := state === Signal.pure stFALLL
  let isFALLR := state === Signal.pure stFALLR
  let isDIGL := state === Signal.pure stDIGL
  let isDIGR := state === Signal.pure stDIGR
  
  let walk_left  := Signal.mux isWL (Signal.pure 1#1) (Signal.pure 0#1)
  let walk_right := Signal.mux isWR (Signal.pure 1#1) (Signal.pure 0#1)
  let aaah       := Signal.mux (isFALLL ||| isFALLR) (Signal.pure 1#1) (Signal.pure 0#1)
  let digging    := Signal.mux (isDIGL ||| isDIGR) (Signal.pure 1#1) (Signal.pure 0#1)
  
  -- Bundle all four outputs as nested pairs
  bundle2 walk_left (bundle2 walk_right (bundle2 aaah digging))

#synthesizeVerilog prob155_lemmings4
