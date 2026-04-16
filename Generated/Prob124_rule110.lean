import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

set_option exponentiation.threshold 1024

/-- Compute Rule 110 next state from current state.
    Rule 110: next = ~(L&C&R | ~L&~C&~R | L&~C&~R)
    where L = left neighbor (q>>>1), C = center (q), R = right neighbor (q<<<1)
    Boundaries are treated as 0. -/
def rule110NextState (q : BitVec 512) : BitVec 512 :=
  let left   : BitVec 512 := BitVec.ushiftRight q 1   -- left neighbor (q shifted right, top=0)
  let right  : BitVec 512 := BitVec.shiftLeft q 1     -- right neighbor (q shifted left, bot=0)
  let center : BitVec 512 := q
  let notCenter := ~~~center
  let notRight  := ~~~right
  let notLeft   := ~~~left
  let case111 := left &&& center &&& right      -- L=1, C=1, R=1 → 0
  let case000 := notLeft &&& notCenter &&& notRight -- L=0, C=0, R=0 → 0
  let case100 := left &&& notCenter &&& notRight    -- L=1, C=0, R=0 → 0
  ~~~(case111 ||| case000 ||| case100)

/-- Rule 110 cellular automaton: 512-cell system advancing one step per clock.
    On load, state is set to data; otherwise computes Rule 110 next state.
    Boundaries are treated as 0. -/
def prob124_rule110 {dom : DomainConfig}
    (load : Signal dom Bool)
    (data : Signal dom (BitVec 512))
    : Signal dom (BitVec 512) :=
  Signal.loop fun q =>
    let nextState := Signal.map rule110NextState q
    let nextVal := Signal.mux load data nextState
    Signal.register (0 : BitVec 512) nextVal

#synthesizeVerilog prob124_rule110
