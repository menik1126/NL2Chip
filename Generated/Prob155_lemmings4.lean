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
    (bump_left bump_right ground dig : Signal dom Bool)
    : Signal dom ((BitVec 1 × BitVec 1) × (BitVec 1 × BitVec 1)) :=
  -- Combined state: 3 bits for FSM state + 5 bits for fall counter = 8 bits
  let stateAndOutputs := Signal.loop fun (combined : Signal dom (BitVec 8)) =>
    let state := Signal.map (fun x => x.extractLsb 2 0) combined
    let fall_counter := Signal.map (fun x => x.extractLsb 7 3) combined
    
    -- State comparisons
    let isWL    := state === Signal.pure stWL
    let isWR    := state === Signal.pure stWR
    let isFALLL := state === Signal.pure stFALLL
    let isFALLR := state === Signal.pure stFALLR
    let isDIGL  := state === Signal.pure stDIGL
    let isDIGR  := state === Signal.pure stDIGR
    
    -- Fall counter >= 20 check (20 = 0b10100)
    let counterAtMax := fall_counter === Signal.pure 20#5
    
    -- Next state logic for each current state
    -- WL: if !ground -> FALLL, else if dig -> DIGL, else if bump_left -> WR, else WL
    let nextFromWL := Signal.mux (~~~ground) (Signal.pure stFALLL)
      (Signal.mux dig (Signal.pure stDIGL)
        (Signal.mux bump_left (Signal.pure stWR) (Signal.pure stWL)))
    
    -- WR: if !ground -> FALLR, else if dig -> DIGR, else if bump_right -> WL, else WR
    let nextFromWR := Signal.mux (~~~ground) (Signal.pure stFALLR)
      (Signal.mux dig (Signal.pure stDIGR)
        (Signal.mux bump_right (Signal.pure stWL) (Signal.pure stWR)))
    
    -- FALLL: if ground -> (counter >= 20 ? DEAD : WL), else FALLL
    let nextFromFALLL := Signal.mux ground
      (Signal.mux counterAtMax (Signal.pure stDEAD) (Signal.pure stWL))
      (Signal.pure stFALLL)
    
    -- FALLR: if ground -> (counter >= 20 ? DEAD : WR), else FALLR
    let nextFromFALLR := Signal.mux ground
      (Signal.mux counterAtMax (Signal.pure stDEAD) (Signal.pure stWR))
      (Signal.pure stFALLR)
    
    -- DIGL: if ground -> DIGL, else FALLL
    let nextFromDIGL := Signal.mux ground (Signal.pure stDIGL) (Signal.pure stFALLL)
    
    -- DIGR: if ground -> DIGR, else FALLR
    let nextFromDIGR := Signal.mux ground (Signal.pure stDIGR) (Signal.pure stFALLR)
    
    -- DEAD: stay DEAD
    let nextFromDEAD := Signal.pure stDEAD
    
    -- Mux tree to select next state based on current state
    let nextState := Signal.mux isWL nextFromWL
      (Signal.mux isWR nextFromWR
        (Signal.mux isFALLL nextFromFALLL
          (Signal.mux isFALLR nextFromFALLR
            (Signal.mux isDIGL nextFromDIGL
              (Signal.mux isDIGR nextFromDIGR nextFromDEAD)))))
    
    -- Fall counter logic
    let isFalling := isFALLL ||| isFALLR
    -- Counter < 20: check if counter != 20
    let notAtMax := ~~~counterAtMax
    let shouldInc := isFalling &&& notAtMax
    let nextCounter := Signal.mux shouldInc
      (fall_counter + 1#5)
      (Signal.mux isFalling fall_counter (Signal.pure 0#5))
    
    -- Apply async reset
    let nextStateWithReset := Signal.mux areset (Signal.pure stWL) nextState
    let nextCounterWithReset := Signal.mux areset (Signal.pure 0#5) nextCounter
    
    -- Combine state and counter into 8 bits
    let stateExt := Signal.map (fun s => s.zeroExtend 8) nextStateWithReset
    let counterExt := Signal.map (fun c => c.zeroExtend 8) nextCounterWithReset
    let counterShifted := counterExt <<< 3#8
    let nextCombined := stateExt ||| counterShifted
    
    Signal.register 0#8 nextCombined
  
  -- Extract state from combined
  let state := Signal.map (fun x => x.extractLsb 2 0) stateAndOutputs
  
  -- Derive outputs from state using === comparisons
  let walk_left  := state === Signal.pure stWL
  let walk_right := state === Signal.pure stWR
  let isFALLL_out := state === Signal.pure stFALLL
  let isFALLR_out := state === Signal.pure stFALLR
  let aaah       := isFALLL_out ||| isFALLR_out
  let isDIGL_out := state === Signal.pure stDIGL
  let isDIGR_out := state === Signal.pure stDIGR
  let digging    := isDIGL_out ||| isDIGR_out
  
  -- Convert Bool to BitVec 1 using Signal.mux
  let walk_left_bv  := Signal.mux walk_left (Signal.pure 1#1) (Signal.pure 0#1)
  let walk_right_bv := Signal.mux walk_right (Signal.pure 1#1) (Signal.pure 0#1)
  let aaah_bv       := Signal.mux aaah (Signal.pure 1#1) (Signal.pure 0#1)
  let digging_bv    := Signal.mux digging (Signal.pure 1#1) (Signal.pure 0#1)
  
  -- Bundle 4 outputs: ((walk_left, walk_right), (aaah, digging))
  bundle2 (bundle2 walk_left_bv walk_right_bv) (bundle2 aaah_bv digging_bv)

#synthesizeVerilog prob155_lemmings4
