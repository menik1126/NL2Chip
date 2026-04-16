import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding (3-bit)
private abbrev stA : BitVec 3 := 0#3
private abbrev stB : BitVec 3 := 1#3
private abbrev stC : BitVec 3 := 2#3
private abbrev stD : BitVec 3 := 3#3
private abbrev stE : BitVec 3 := 4#3
private abbrev stF : BitVec 3 := 5#3

/-- Helper: FSM state register for the 6-state Moore machine -/
private def fsmState {dom : DomainConfig}
    (reset : Signal dom Bool)
    (w : Signal dom Bool)
    : Signal dom (BitVec 3) :=
  Signal.loop fun (state : Signal dom (BitVec 3)) =>
    let isA := state === Signal.pure stA
    let isB := state === Signal.pure stB
    let isC := state === Signal.pure stC
    let isD := state === Signal.pure stD
    let isE := state === Signal.pure stE
    let isF := state === Signal.pure stF
    -- When w=1: A→B, B→C, C→E, D→F, E→E, F→C
    let nextW1 :=
      hw_cond (Signal.pure stA)
      | isA => Signal.pure stB
      | isB => Signal.pure stC
      | isC => Signal.pure stE
      | isD => Signal.pure stF
      | isE => Signal.pure stE
      | isF => Signal.pure stC
    -- When w=0: A→A, B→D, C→D, D→A, E→D, F→D
    let nextW0 :=
      hw_cond (Signal.pure stA)
      | isA => Signal.pure stA
      | isB => Signal.pure stD
      | isC => Signal.pure stD
      | isD => Signal.pure stA
      | isE => Signal.pure stD
      | isF => Signal.pure stD
    let nextState := Signal.mux w nextW1 nextW0
    let nextWithReset := Signal.mux reset (Signal.pure stA) nextState
    Signal.register stA nextWithReset

/-- Moore FSM with 6 states (A-F), synchronous reset to A.
    Output z=1 in states E and F.
    Transitions:
      A: w=1→B, w=0→A
      B: w=1→C, w=0→D
      C: w=1→E, w=0→D
      D: w=1→F, w=0→A
      E: w=1→E, w=0→D
      F: w=1→C, w=0→D -/
def prob138_2012_q2fsm {dom : DomainConfig}
    (reset : Signal dom Bool)
    (w : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  let state := fsmState reset w
  let isE := state === Signal.pure stE
  let isF := state === Signal.pure stF
  Signal.mux (isE ||| isF) (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob138_2012_q2fsm
