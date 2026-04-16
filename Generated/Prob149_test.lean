import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding (need 3 bits for 6 states)
private abbrev stWL : BitVec 3 := 0#3     -- Walk Left
private abbrev stWR : BitVec 3 := 1#3     -- Walk Right
private abbrev stFALLL : BitVec 3 := 2#3  -- Fall Left
private abbrev stFALLR : BitVec 3 := 3#3  -- Fall Right
private abbrev stDIGL : BitVec 3 := 4#3   -- Dig Left
private abbrev stDIGR : BitVec 3 := 5#3   -- Dig Right

/-- Lemmings 3 FSM: walk, fall, dig with direction tracking.
    Returns concatenated outputs: {digging, aaah, walk_right, walk_left}. -/
def prob149_test {dom : DomainConfig}
    (areset : Signal dom Bool)
    (bump_left bump_right ground dig : Signal dom Bool)
    : Signal dom (BitVec 4) :=
  -- State register via Signal.loop
  let state : Signal dom (BitVec 3) :=
    Signal.loop fun (state : Signal dom (BitVec 3)) =>
      let isWL := state === (Signal.pure stWL)
      let isWR := state === (Signal.pure stWR)
      let isFALLL := state === (Signal.pure stFALLL)
      let isFALLR := state === (Signal.pure stFALLR)
      let isDIGL := state === (Signal.pure stDIGL)
      
      -- Next state logic for WL: priority is !ground > dig > bump_left
      let nextFromWL := 
        Signal.mux (~~~ground) (Signal.pure stFALLL)
          (Signal.mux dig (Signal.pure stDIGL)
            (Signal.mux bump_left (Signal.pure stWR) (Signal.pure stWL)))
      
      -- Next state logic for WR: priority is !ground > dig > bump_right
      let nextFromWR :=
        Signal.mux (~~~ground) (Signal.pure stFALLR)
          (Signal.mux dig (Signal.pure stDIGR)
            (Signal.mux bump_right (Signal.pure stWL) (Signal.pure stWR)))
      
      -- Next state logic for FALLL: ground → WL, else stay FALLL
      let nextFromFALLL := Signal.mux ground (Signal.pure stWL) (Signal.pure stFALLL)
      
      -- Next state logic for FALLR: ground → WR, else stay FALLR
      let nextFromFALLR := Signal.mux ground (Signal.pure stWR) (Signal.pure stFALLR)
      
      -- Next state logic for DIGL: ground → stay DIGL, else → FALLL
      let nextFromDIGL := Signal.mux ground (Signal.pure stDIGL) (Signal.pure stFALLL)
      
      -- Next state logic for DIGR: ground → stay DIGR, else → FALLR
      let nextFromDIGR := Signal.mux ground (Signal.pure stDIGR) (Signal.pure stFALLR)
      
      -- Combine all state transitions
      let nextState :=
        Signal.mux isWL nextFromWL
          (Signal.mux isWR nextFromWR
            (Signal.mux isFALLL nextFromFALLL
              (Signal.mux isFALLR nextFromFALLR
                (Signal.mux isDIGL nextFromDIGL nextFromDIGR))))
      
      -- Apply async reset (modeled as sync): areset → WL
      let nextWithReset := Signal.mux areset (Signal.pure stWL) nextState
      
      -- Register with initial value WL
      Signal.register stWL nextWithReset
  
  -- Derive outputs from state
  
  -- Test outputs
  let fr2_sig := Signal.mux (state === (Signal.pure stWL)) (Signal.pure 1#1) (Signal.pure 0#1)
  let fr1_sig := Signal.mux (state === (Signal.pure stWR)) (Signal.pure 1#1) (Signal.pure 0#1)
  let fr0_sig := Signal.mux (state === (Signal.pure stFALLL)) (Signal.pure 1#1) (Signal.pure 0#1)
  let dfr_sig := Signal.mux (state === (Signal.pure stFALLR)) (Signal.pure 1#1) (Signal.pure 0#1)
  
  fr2_sig ++ fr1_sig ++ fr0_sig ++ dfr_sig

#synthesizeVerilog prob149_test
