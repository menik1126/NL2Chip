import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding (3 bits for 7 states)
private abbrev stWL    : BitVec 3 := 0#3  -- Walking Left
private abbrev stWR    : BitVec 3 := 1#3  -- Walking Right
private abbrev stFALLL : BitVec 3 := 2#3  -- Falling Left
private abbrev stFALLR : BitVec 3 := 3#3  -- Falling Right
private abbrev stDIGL  : BitVec 3 := 4#3  -- Digging Left
private abbrev stDIGR  : BitVec 3 := 5#3  -- Digging Right
private abbrev stDEAD  : BitVec 3 := 6#3  -- Dead (splattered)

/-- Lemmings FSM with falling, digging, and splatting.
    Returns ((walk_left, walk_right), (aaah, digging)) as nested pairs. -/
def prob155_lemmings4 {dom : DomainConfig}
    (areset : Signal dom Bool)
    (bump_left bump_right ground dig : Signal dom Bool)
    : Signal dom ((BitVec 1 × BitVec 1) × (BitVec 1 × BitVec 1)) :=
  -- Combined state in a single 8-bit register: [7:5] = state, [4:0] = fall_counter
  let combined : Signal dom (BitVec 8) :=
    Signal.loop fun (reg : Signal dom (BitVec 8)) =>
      -- Extract state (bits 7:5) and counter (bits 4:0)
      let stateShifted := reg >>> 5#8
      let state := stateShifted &&& 7#8  -- Mask to get 3 bits
      let fall_counter := reg &&& 31#8    -- Mask to get 5 bits
      
      -- State comparisons (state is now BitVec 8, so extend state constants)
      let isWL    := state === Signal.pure (stWL.zeroExtend 8)
      let isWR    := state === Signal.pure (stWR.zeroExtend 8)
      let isFALLL := state === Signal.pure (stFALLL.zeroExtend 8)
      let isFALLR := state === Signal.pure (stFALLR.zeroExtend 8)
      let isDIGL  := state === Signal.pure (stDIGL.zeroExtend 8)
      let isDIGR  := state === Signal.pure (stDIGR.zeroExtend 8)
      
      -- Fall counter >= 20 check (counter is BitVec 8, so extend)
      let counterOverMax := fall_counter === Signal.pure 20#8
      
      -- Next state logic (priority: fall > dig > bump) - return BitVec 8
      -- WL state
      let nextFromWL := 
        Signal.mux (~~~ground) (Signal.pure (stFALLL.zeroExtend 8))
          (Signal.mux dig (Signal.pure (stDIGL.zeroExtend 8))
            (Signal.mux bump_left (Signal.pure (stWR.zeroExtend 8)) (Signal.pure (stWL.zeroExtend 8))))
      
      -- WR state
      let nextFromWR :=
        Signal.mux (~~~ground) (Signal.pure (stFALLR.zeroExtend 8))
          (Signal.mux dig (Signal.pure (stDIGR.zeroExtend 8))
            (Signal.mux bump_right (Signal.pure (stWL.zeroExtend 8)) (Signal.pure (stWR.zeroExtend 8))))
      
      -- FALLL state: if ground, check counter; if counter >= 20, go DEAD, else go WL
      let nextFromFALLL :=
        Signal.mux ground
          (Signal.mux counterOverMax (Signal.pure (stDEAD.zeroExtend 8)) (Signal.pure (stWL.zeroExtend 8)))
          (Signal.pure (stFALLL.zeroExtend 8))
      
      -- FALLR state: if ground, check counter; if counter >= 20, go DEAD, else go WR
      let nextFromFALLR :=
        Signal.mux ground
          (Signal.mux counterOverMax (Signal.pure (stDEAD.zeroExtend 8)) (Signal.pure (stWR.zeroExtend 8)))
          (Signal.pure (stFALLR.zeroExtend 8))
      
      -- DIGL state: if no ground, fall left; else keep digging
      let nextFromDIGL :=
        Signal.mux ground (Signal.pure (stDIGL.zeroExtend 8)) (Signal.pure (stFALLL.zeroExtend 8))
      
      -- DIGR state: if no ground, fall right; else keep digging
      let nextFromDIGR :=
        Signal.mux ground (Signal.pure (stDIGR.zeroExtend 8)) (Signal.pure (stFALLR.zeroExtend 8))
      
      -- DEAD state: stay dead
      let nextFromDEAD := Signal.pure (stDEAD.zeroExtend 8)
      
      -- Mux all next states based on current state
      let nextState :=
        Signal.mux isWL nextFromWL
          (Signal.mux isWR nextFromWR
            (Signal.mux isFALLL nextFromFALLL
              (Signal.mux isFALLR nextFromFALLR
                (Signal.mux isDIGL nextFromDIGL
                  (Signal.mux isDIGR nextFromDIGR nextFromDEAD)))))
      
      -- Fall counter logic: increment if falling (and < 20), else reset to 0
      let isFalling := isFALLL ||| isFALLR
      let counterAt20 := fall_counter === Signal.pure 20#8
      let counterLessThan20 := ~~~counterAt20
      let shouldIncrement := isFalling &&& counterLessThan20
      let nextCounter := Signal.mux isFalling
        (Signal.mux shouldIncrement (fall_counter + 1#8) fall_counter)
        (Signal.pure 0#8)
      
      -- Apply async reset (modeled as sync)
      let nextStateWithReset := Signal.mux areset (Signal.pure (stWL.zeroExtend 8)) nextState
      let nextCounterWithReset := Signal.mux areset (Signal.pure 0#8) nextCounter
      
      -- Combine state and counter into single 8-bit value: [7:5] = state, [4:0] = counter
      -- State is already in bits [2:0], shift left by 5 to get to [7:5]
      let nextCombined := (nextStateWithReset <<< 5#8) ||| nextCounterWithReset
      
      Signal.register 0#8 nextCombined
  
  -- Extract state from combined register
  let stateShifted := combined >>> 5#8
  let state := stateShifted &&& 7#8
  
  -- Derive outputs from state (compare with extended state constants)
  let walk_left_bool  := state === Signal.pure (stWL.zeroExtend 8)
  let walk_right_bool := state === Signal.pure (stWR.zeroExtend 8)
  let aaah_bool       := (state === Signal.pure (stFALLL.zeroExtend 8)) ||| (state === Signal.pure (stFALLR.zeroExtend 8))
  let digging_bool    := (state === Signal.pure (stDIGL.zeroExtend 8)) ||| (state === Signal.pure (stDIGR.zeroExtend 8))
  
  -- Convert Bool to BitVec 1
  let walk_left  := Signal.mux walk_left_bool  (Signal.pure 1#1) (Signal.pure 0#1)
  let walk_right := Signal.mux walk_right_bool (Signal.pure 1#1) (Signal.pure 0#1)
  let aaah       := Signal.mux aaah_bool       (Signal.pure 1#1) (Signal.pure 0#1)
  let digging    := Signal.mux digging_bool    (Signal.pure 1#1) (Signal.pure 0#1)
  
  -- Bundle all four outputs as nested pairs: ((walk_left, walk_right), (aaah, digging))
  bundle2 (bundle2 walk_left walk_right) (bundle2 aaah digging)

#synthesizeVerilog prob155_lemmings4
