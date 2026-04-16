import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding
private abbrev stB0   : BitVec 3 := 0#3
private abbrev stB1   : BitVec 3 := 1#3
private abbrev stB2   : BitVec 3 := 2#3
private abbrev stB3   : BitVec 3 := 3#3
private abbrev stDone : BitVec 3 := 4#3

/-- FSM that asserts shift_ena for exactly 4 cycles after reset (synchronous active-high).
    States: B0→B1→B2→B3→Done (stays). shift_ena high in B0..B3. -/
def prob095_review2015_fsmshift {dom : DomainConfig}
    (reset : Signal dom Bool) : Signal dom (BitVec 1) :=
  let state : Signal dom (BitVec 3) :=
    Signal.loop fun (state : Signal dom (BitVec 3)) =>
      let nextNormal :=
        hw_cond (Signal.pure stDone)
        | (state === Signal.pure stB0) => Signal.pure stB1
        | (state === Signal.pure stB1) => Signal.pure stB2
        | (state === Signal.pure stB2) => Signal.pure stB3
        | (state === Signal.pure stB3) => Signal.pure stDone
      let nextState := Signal.mux reset (Signal.pure stB0) nextNormal
      Signal.register stB0 nextState
  let isDone := state === Signal.pure stDone
  Signal.mux isDone (Signal.pure 0#1) (Signal.pure 1#1)

#synthesizeVerilog prob095_review2015_fsmshift
