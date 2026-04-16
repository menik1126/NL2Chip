import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding: A=0, B=1, C=2, D=3
private abbrev stA : BitVec 2 := 0#2
private abbrev stB : BitVec 2 := 1#2
private abbrev stC : BitVec 2 := 2#2
private abbrev stD : BitVec 2 := 3#2

/-- Moore FSM with 4 states (A=0, B=1, C=2, D=3), synchronous active-high reset to A.
    State transitions:
      A: in=0→A, in=1→B, out=0
      B: in=0→C, in=1→B, out=0
      C: in=0→A, in=1→D, out=0
      D: in=0→C, in=1→B, out=1
    Output is 1 only in state D. -/
def prob120_fsm3s {dom : DomainConfig}
    (reset : Signal dom Bool)
    (inp : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  -- State register via Signal.loop
  let state : Signal dom (BitVec 2) :=
    Signal.loop fun (state : Signal dom (BitVec 2)) =>
      let isA := state === (Signal.pure stA)
      let isB := state === (Signal.pure stB)
      let isC := state === (Signal.pure stC)
      -- Next state when in=0: A→A, B→C, C→A, D→C
      let nextIf0 : Signal dom (BitVec 2) :=
        hw_cond (Signal.pure stC)     -- default: D→C
        | isA => Signal.pure stA      -- A→A
        | isB => Signal.pure stC      -- B→C
        | isC => Signal.pure stA      -- C→A
      -- Next state when in=1: A→B, B→B, C→D, D→B
      let nextIf1 : Signal dom (BitVec 2) :=
        hw_cond (Signal.pure stB)     -- default: D→B
        | isA => Signal.pure stB      -- A→B
        | isB => Signal.pure stB      -- B→B
        | isC => Signal.pure stD      -- C→D
      -- Select next state based on input
      let nextState := Signal.mux inp nextIf1 nextIf0
      -- Apply synchronous active-high reset: reset → A
      let nextWithReset := Signal.mux reset (Signal.pure stA) nextState
      -- Register state with initial value A
      Signal.register stA nextWithReset
  -- Output: 1 when in state D, 0 otherwise
  -- state == D means state == 3#2, extract as 1-bit using mux
  let isD := state === (Signal.pure stD)
  Signal.mux isD (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob120_fsm3s
