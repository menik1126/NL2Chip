import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- FSM output logic and next state logic for 2014_q3c -/
def prob134_2014_q3c {dom : DomainConfig}
    (x : Signal dom (BitVec 1)) (y : Signal dom (BitVec 3))
    : Signal dom (BitVec 1 × BitVec 1) :=
  -- Compute Y0
  bundle2 
    (Signal.map (fun (pair : BitVec 1 × BitVec 3) =>
      let x_val := pair.1
      let y_val := pair.2
      -- Concatenate {y[2:0], x} to form a 4-bit value
      let combined := (y_val.zeroExtend 4 <<< 1) ||| x_val.zeroExtend 4
      -- Y0 lookup table based on combined value
      -- 0000 (0) → 0, 0001 (1) → 1, 0010 (2) → 1, 0011 (3) → 0
      -- 0100 (4) → 0, 0101 (5) → 1, 0110 (6) → 1, 0111 (7) → 0
      -- 1000 (8) → 1, 1001 (9) → 0
      let lut := 0b0110110110#10 : BitVec 10
      let bit := (lut >>> combined.toNat).getLsb 0
      BitVec.ofBool bit
    ) (bundle2 x y))
    (Signal.map (fun (y_val : BitVec 3) =>
      -- z = 1 when y is 011 (3) or 100 (4)
      let is_3 := y_val == 3#3
      let is_4 := y_val == 4#3
      let result := is_3 || is_4
      BitVec.ofBool result
    ) y)

#synthesizeVerilog prob134_2014_q3c
