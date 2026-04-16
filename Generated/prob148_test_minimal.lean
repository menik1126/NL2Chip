import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Minimal test: FSM with 2-bit state and 3-bit output
private abbrev stA2 : BitVec 2 := 0#2
private abbrev stB2 : BitVec 2 := 1#2

/-- Minimal test FSM -/
def prob148_test_minimal {dom : DomainConfig}
    (resetn : Signal dom Bool)
    : Signal dom (BitVec 3) :=
  let state : Signal dom (BitVec 2) :=
    Signal.loop fun (state : Signal dom (BitVec 2)) =>
      let notResetn := ~~~resetn
      let nextWithReset := Signal.mux notResetn (Signal.pure stA2) state
      Signal.register stA2 nextWithReset
  let isB := state === (Signal.pure stB2)
  Signal.mux isB (Signal.pure 1#3) (Signal.pure 0#3)

#synthesizeVerilog prob148_test_minimal
