import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Fixed-point adder with parameterized precision.
    Performs signed addition/subtraction based on sign bits (MSB). -/
def fixed_point_adder {dom : DomainConfig}
    (a b : Signal dom (BitVec 32)) : Signal dom (BitVec 32) :=
  -- Extract sign bits (MSB) by shifting right 31 bits
  let signA := (a >>> 31#32) &&& 1#32
  let signB := (b >>> 31#32) &&& 1#32
  let signsEqual := signA === signB
  
  -- Extract magnitudes (lower 31 bits)
  let magA := a &&& 0x7FFFFFFF#32
  let magB := b &&& 0x7FFFFFFF#32
  
  -- Case 1: same signs - add magnitudes, preserve sign
  let addResult := magA + magB
  let addFinal := addResult ||| (signA <<< 31#32)
  
  -- Case 2: different signs - subtract
  -- Compare by subtracting: if (a - b) has MSB=0, then a >= b
  let diff := magA - magB
  let diffSign := (diff >>> 31#32) &&& 1#32
  let aGreaterOrEqual := diffSign === 0#32
  
  -- Determine subtraction result and sign
  let subResult := Signal.mux aGreaterOrEqual (magA - magB) (magB - magA)
  let isZero := subResult === 0#32
  
  -- Sign logic for subtraction:
  -- If a is positive (signA=0) and b is negative (signB=1):
  --   if a > b: result is positive (sign=0)
  --   if b > a: result is negative if non-zero (sign=1)
  -- If a is negative (signA=1) and b is positive (signB=0):
  --   if a > b: result is negative if non-zero (sign=1)
  --   if b > a: result is positive (sign=0)
  let aIsPositive := signA === 0#32
  let resultSign := Signal.mux isZero
    (Signal.pure 0#32)  -- zero is always positive
    (Signal.mux aIsPositive
      (Signal.mux aGreaterOrEqual (Signal.pure 0#32) (Signal.pure 1#32))
      (Signal.mux aGreaterOrEqual (Signal.pure 1#32) (Signal.pure 0#32)))
  
  let subFinal := subResult ||| (resultSign <<< 31#32)
  
  Signal.mux signsEqual addFinal subFinal

#synthesizeVerilog fixed_point_adder
