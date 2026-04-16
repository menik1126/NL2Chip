import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Karnaugh map function: computes f from 4-bit input x.
    f=1 for x in {0,1,4,5,6,12,14,15}, f=0 otherwise. -/
def prob113_2012_q1g {dom : DomainConfig}
    (x : Signal dom (BitVec 4)) : Signal dom (BitVec 1) :=
  hw_cond (Signal.pure 0#1)
    | (x === Signal.pure 0#4)  => Signal.pure 1#1
    | (x === Signal.pure 1#4)  => Signal.pure 1#1
    | (x === Signal.pure 4#4)  => Signal.pure 1#1
    | (x === Signal.pure 5#4)  => Signal.pure 1#1
    | (x === Signal.pure 6#4)  => Signal.pure 1#1
    | (x === Signal.pure 12#4) => Signal.pure 1#1
    | (x === Signal.pure 14#4) => Signal.pure 1#1
    | (x === Signal.pure 15#4) => Signal.pure 1#1

#synthesizeVerilog prob113_2012_q1g
