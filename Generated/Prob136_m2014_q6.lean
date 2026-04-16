import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding: A=0, B=1, C=2, D=3, E=4, F=5
private abbrev stA : BitVec 3 := 0#3
private abbrev stB : BitVec 3 := 1#3
private abbrev stC : BitVec 3 := 2#3
private abbrev stD : BitVec 3 := 3#3
private abbrev stE : BitVec 3 := 4#3
private abbrev stF : BitVec 3 := 5#3

/-- Moore FSM with 6 states (A-F). Output z=1 in states E and F.
    Synchronous reset to state A.
    Transitions:
      A --w=0--> B, A --w=1--> A
      B --w=0--> C, B --w=1--> D
      C --w=0--> E, C --w=1--> D
      D --w=0--> F, D --w=1--> A
      E --w=0--> E, E --w=1--> D
      F --w=0--> C, F --w=1--> D -/
def prob136_m2014_q6 {dom : DomainConfig}
    (reset : Signal dom Bool)
    (w : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  -- State register using Signal.loop (returns state BitVec 3)
  let state : Signal dom (BitVec 3) :=
    Signal.loop fun (state : Signal dom (BitVec 3)) =>
      let isA := state === Signal.pure stA
      let isB := state === Signal.pure stB
      let isC := state === Signal.pure stC
      let isD := state === Signal.pure stD
      let isE := state === Signal.pure stE
      -- F is the else/default case

      -- Next state when w=0: A→B, B→C, C→E, D→F, E→E, F→C
      let next0 :=
        hw_cond (Signal.pure stC)
        | isA => Signal.pure stB
        | isB => Signal.pure stC
        | isC => Signal.pure stE
        | isD => Signal.pure stF
        | isE => Signal.pure stE

      -- Next state when w=1: A→A, B→D, C→D, D→A, E→D, F→D
      let next1 :=
        hw_cond (Signal.pure stD)
        | isA => Signal.pure stA
        | isB => Signal.pure stD
        | isC => Signal.pure stD
        | isD => Signal.pure stA
        | isE => Signal.pure stD

      let nextState := Signal.mux w next1 next0
      let nextWithReset := Signal.mux reset (Signal.pure stA) nextState
      Signal.register stA nextWithReset

  -- Output: z = 1 when state is E or F
  let isStateE := state === Signal.pure stE
  let isStateF := state === Signal.pure stF
  Signal.mux (isStateE ||| isStateF) (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob136_m2014_q6
