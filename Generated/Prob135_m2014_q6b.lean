import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- FSM next-state logic for y[1] bit -/
def prob135_m2014_q6b {dom : DomainConfig}
    (y : Signal dom (BitVec 3)) (w : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  -- Convert w to BitVec
  let w_bv := Signal.mux w (Signal.pure 1#1) (Signal.pure 0#1)
  
  -- Build 4-bit case value by concatenating y (shifted left) and w
  -- We need to extend y from 3 bits to 4 bits, shift left by 1, then OR with w
  -- Since we can't use Signal.map, we'll work with the 3-bit value directly
  -- and use bitwise operations
  
  -- Check each case directly by comparing y and w
  -- State B (001): y == 1
  let is_stateB := y === Signal.pure 0x1#3
  let is_case_2 := is_stateB &&& (~~~w)  -- B with w=0
  let is_case_3 := is_stateB &&& w        -- B with w=1
  
  -- State C (010): y == 2
  let is_stateC := y === Signal.pure 0x2#3
  let is_case_5 := is_stateC &&& w        -- C with w=1
  
  -- State E (100): y == 4
  let is_stateE := y === Signal.pure 0x4#3
  let is_case_9 := is_stateE &&& w        -- E with w=1
  
  -- State F (101): y == 5
  let is_stateF := y === Signal.pure 0x5#3
  let is_case_a := is_stateF &&& (~~~w)  -- F with w=0
  let is_case_b := is_stateF &&& w        -- F with w=1
  
  -- Combine all conditions
  let result := is_case_2 ||| is_case_3 ||| is_case_5 ||| is_case_9 ||| is_case_a ||| is_case_b
  
  Signal.mux result (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob135_m2014_q6b
