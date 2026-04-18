import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Convert Bool signal to BitVec 1 signal -/
def boolToBitVec {dom : DomainConfig} (b : Signal dom Bool) : Signal dom (BitVec 1) :=
  Signal.mux b (Signal.pure 1#1) (Signal.pure 0#1)

/-- 4-digit BCD counter with enable signals for upper digits -/
def prob068_countbcd {dom : DomainConfig}
    (reset : Signal dom Bool) 
    : Signal dom (BitVec 3 × BitVec 16) :=
  let q := Signal.loop fun (q : Signal dom (BitVec 16)) =>
    -- Check if each digit is 9 (using bit masking)
    let digit0_is9 := (q &&& 15#16) === 9#16
    let digit1_is9 := ((q >>> 4#16) &&& 15#16) === 9#16
    let digit2_is9 := ((q >>> 8#16) &&& 15#16) === 9#16
    let digit3_is9 := ((q >>> 12#16) &&& 15#16) === 9#16
    
    -- Compute enable signals
    let enable1 := digit0_is9
    let enable2 := digit0_is9 &&& digit1_is9
    let enable3 := digit0_is9 &&& digit1_is9 &&& digit2_is9
    
    -- Compute next value for digit 0 (always enabled)
    let next0_raw := (q &&& 15#16) + 1#16
    let next0 := Signal.mux (reset ||| digit0_is9) (Signal.pure 0#16) next0_raw
    
    -- Compute next value for digit 1
    let curr1 := (q >>> 4#16) &&& 15#16
    let next1_inc := curr1 + 1#16
    let next1_raw := Signal.mux enable1 next1_inc curr1
    let next1 := Signal.mux (reset ||| (digit1_is9 &&& enable1)) (Signal.pure 0#16) next1_raw
    
    -- Compute next value for digit 2
    let curr2 := (q >>> 8#16) &&& 15#16
    let next2_inc := curr2 + 1#16
    let next2_raw := Signal.mux enable2 next2_inc curr2
    let next2 := Signal.mux (reset ||| (digit2_is9 &&& enable2)) (Signal.pure 0#16) next2_raw
    
    -- Compute next value for digit 3
    let curr3 := (q >>> 12#16) &&& 15#16
    let next3_inc := curr3 + 1#16
    let next3_raw := Signal.mux enable3 next3_inc curr3
    let next3 := Signal.mux (reset ||| (digit3_is9 &&& enable3)) (Signal.pure 0#16) next3_raw
    
    -- Combine all digits
    let nextQ := (next0 &&& 15#16) ||| ((next1 &&& 15#16) <<< 4#16) ||| 
                 ((next2 &&& 15#16) <<< 8#16) ||| ((next3 &&& 15#16) <<< 12#16)
    
    Signal.register 0#16 nextQ
  
  -- Compute enable outputs from q
  let digit0_is9 := (q &&& 15#16) === 9#16
  let digit1_is9 := ((q >>> 4#16) &&& 15#16) === 9#16
  let digit2_is9 := ((q >>> 8#16) &&& 15#16) === 9#16
  
  let enable1 := digit0_is9
  let enable2 := digit0_is9 &&& digit1_is9
  let enable3 := digit0_is9 &&& digit1_is9 &&& digit2_is9
  
  -- Convert enables to BitVec and concatenate
  let ena1_bv := boolToBitVec enable1
  let ena2_bv := boolToBitVec enable2
  let ena3_bv := boolToBitVec enable3
  
  let ena := ena3_bv ++ ena2_bv ++ ena1_bv
  
  bundle2 ena q

#synthesizeVerilog prob068_countbcd
