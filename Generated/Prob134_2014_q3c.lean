import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- FSM combinational logic: given current state y[2:0] and input x,
    compute Y0 (bit 0 of next state) and z (output based on current state).

    State table:
      y=000, x=0 → next=000 (Y0=0), z=0
      y=000, x=1 → next=001 (Y0=1), z=0
      y=001, x=0 → next=001 (Y0=1), z=0
      y=001, x=1 → next=100 (Y0=0), z=0
      y=010, x=0 → next=010 (Y0=0), z=0
      y=010, x=1 → next=001 (Y0=1), z=0
      y=011, x=0 → next=001 (Y0=1), z=1
      y=011, x=1 → next=010 (Y0=0), z=1
      y=100, x=0 → next=011 (Y0=1), z=1
      y=100, x=1 → next=100 (Y0=0), z=1 -/
def prob134_2014_q3c {dom : DomainConfig}
    (x : Signal dom Bool)
    (y : Signal dom (BitVec 3))
    : Signal dom (BitVec 1 × BitVec 1) :=
  -- State comparisons using === on full 3-bit state
  let is000 : Signal dom Bool := y === Signal.pure 0#3
  let is001 : Signal dom Bool := y === Signal.pure 1#3
  let is010 : Signal dom Bool := y === Signal.pure 2#3
  let is011 : Signal dom Bool := y === Signal.pure 3#3
  let is100 : Signal dom Bool := y === Signal.pure 4#3

  -- Y0 computation (bit 0 of next state):
  -- y=000, x=0 → Y0=0; y=000, x=1 → Y0=1  ⟹  Y0 = x
  -- y=001, x=0 → Y0=1; y=001, x=1 → Y0=0  ⟹  Y0 = ~x
  -- y=010, x=0 → Y0=0; y=010, x=1 → Y0=1  ⟹  Y0 = x
  -- y=011, x=0 → Y0=1; y=011, x=1 → Y0=0  ⟹  Y0 = ~x
  -- y=100, x=0 → Y0=1; y=100, x=1 → Y0=0  ⟹  Y0 = ~x
  let Y0_when000 := Signal.mux x (Signal.pure 1#1) (Signal.pure 0#1)
  let Y0_when001 := Signal.mux x (Signal.pure 0#1) (Signal.pure 1#1)
  let Y0_when010 := Signal.mux x (Signal.pure 1#1) (Signal.pure 0#1)
  let Y0_when011 := Signal.mux x (Signal.pure 0#1) (Signal.pure 1#1)
  let Y0_when100 := Signal.mux x (Signal.pure 0#1) (Signal.pure 1#1)

  let Y0 := hw_cond (Signal.pure 0#1)
    | is000 => Y0_when000
    | is001 => Y0_when001
    | is010 => Y0_when010
    | is011 => Y0_when011
    | is100 => Y0_when100

  -- z output: based on present state only
  -- z=1 for y=011 or y=100, z=0 otherwise
  let z_bool : Signal dom Bool := is011 ||| is100
  let z := Signal.mux z_bool (Signal.pure 1#1) (Signal.pure 0#1)

  bundle2 Y0 z

#synthesizeVerilog prob134_2014_q3c
