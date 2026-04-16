import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

set_option exponentiation.threshold 1024

/-- Compute Rule 90 next state from current state.
    Rule 90: next[i] = q[i-1] XOR q[i+1], boundaries are 0. -/
def rule90NextState (q : BitVec 512) : BitVec 512 :=
  -- right_shift gives left neighbour: q[i+1] -> position i
  let left_neighbour : BitVec 512 := BitVec.ushiftRight q 1
  -- left_shift gives right neighbour: q[i-1] -> position i
  let right_neighbour : BitVec 512 := BitVec.shiftLeft q 1
  left_neighbour ^^^ right_neighbour

/-- Rule 90 cellular automaton: 512-cell system where each cell's next state
    is the XOR of its two neighbours. Boundaries are zero (off).
    When load is high, the state is loaded with data. -/
def prob108_rule90 {dom : DomainConfig}
    (load : Signal dom Bool)
    (data : Signal dom (BitVec 512)) : Signal dom (BitVec 512) :=
  Signal.loop fun q =>
    let nextState := Signal.map rule90NextState q
    let nextVal := Signal.mux load data nextState
    Signal.register (0 : BitVec 512) nextVal

#synthesizeVerilog prob108_rule90
