import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding: A=2'b00, B=2'b01, C=2'b10, D=2'b11
private abbrev stA : BitVec 2 := 0#2
private abbrev stB : BitVec 2 := 1#2
private abbrev stC : BitVec 2 := 2#2
private abbrev stD : BitVec 2 := 3#2

/-- Moore FSM combinational logic: given current state and input, compute next_state and out.
    State transition:
      A: in=0 → A, in=1 → B, out=0
      B: in=0 → C, in=1 → B, out=0
      C: in=0 → A, in=1 → D, out=0
      D: in=0 → C, in=1 → B, out=1
    State encoding: A=00, B=01, C=10, D=11 -/
def prob100_fsm3comb {dom : DomainConfig}
    (inp : Signal dom Bool)
    (state : Signal dom (BitVec 2))
    : Signal dom (BitVec 2 × BitVec 1) :=
  -- State comparisons
  let isA := state === Signal.pure stA
  let isB := state === Signal.pure stB
  let isC := state === Signal.pure stC
  let isD := state === Signal.pure stD

  -- Next state for each state:
  -- A: in=0 → A, in=1 → B
  let nextFromA := Signal.mux inp (Signal.pure stB) (Signal.pure stA)
  -- B: in=0 → C, in=1 → B
  let nextFromB := Signal.mux inp (Signal.pure stB) (Signal.pure stC)
  -- C: in=0 → A, in=1 → D
  let nextFromC := Signal.mux inp (Signal.pure stD) (Signal.pure stA)
  -- D: in=0 → C, in=1 → B
  let nextFromD := Signal.mux inp (Signal.pure stB) (Signal.pure stC)

  -- Mux tree to select next state
  let next_state := hw_cond (Signal.pure stA)
    | isA => nextFromA
    | isB => nextFromB
    | isC => nextFromC
    | isD => nextFromD

  -- Output: out = (state == D)
  let out := Signal.mux isD (Signal.pure 1#1) (Signal.pure 0#1)

  bundle2 next_state out

#synthesizeVerilog prob100_fsm3comb
