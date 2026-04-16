import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding: A=0, B=1, C=2, D=3
private abbrev stA : BitVec 2 := 0#2
private abbrev stB : BitVec 2 := 1#2
private abbrev stC : BitVec 2 := 2#2
private abbrev stD : BitVec 2 := 3#2

/-- Test: FSM with r input -/
def prob148_test2 {dom : DomainConfig}
    (resetn : Signal dom Bool)
    (r : Signal dom (BitVec 3))
    : Signal dom (BitVec 3) :=
  let r0 : Signal dom Bool := Signal.map (fun x => x.getLsb 0) r
  let state : Signal dom (BitVec 2) :=
    Signal.loop fun (state : Signal dom (BitVec 2)) =>
      let isA := state === (Signal.pure stA)
      let nextFromA := Signal.mux r0 (Signal.pure stB) (Signal.pure stA)
      let nextState := Signal.mux isA nextFromA state
      let notResetn := ~~~resetn
      let nextWithReset := Signal.mux notResetn (Signal.pure stA) nextState
      Signal.register stA nextWithReset
  let isB := state === (Signal.pure stB)
  Signal.mux isB (Signal.pure 1#3) (Signal.pure 0#3)

#synthesizeVerilog prob148_test2
