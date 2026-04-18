import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Implements Karnaugh map logic for 4-bit input -/
def prob113_2012_q1g {dom : DomainConfig}
    (x : Signal dom (BitVec 4)) : Signal dom (BitVec 1) :=
  -- Check if x equals any of the values where f=1
  let eq0 := x === Signal.pure 0#4
  let eq1 := x === Signal.pure 1#4
  let eq4 := x === Signal.pure 4#4
  let eq5 := x === Signal.pure 5#4
  let eq6 := x === Signal.pure 6#4
  let eq8 := x === Signal.pure 8#4
  let eq14 := x === Signal.pure 14#4
  let eq15 := x === Signal.pure 15#4
  
  -- OR all conditions
  let cond1 := eq0 ||| eq1
  let cond2 := cond1 ||| eq4
  let cond3 := cond2 ||| eq5
  let cond4 := cond3 ||| eq6
  let cond5 := cond4 ||| eq8
  let cond6 := cond5 ||| eq14
  let result := cond6 ||| eq15
  
  Signal.mux result (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob113_2012_q1g
