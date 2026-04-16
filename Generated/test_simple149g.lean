import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

private abbrev stA2 : BitVec 3 := 0#3
private abbrev stB1 : BitVec 3 := 1#3

def testFSM7 {dom : DomainConfig}
    (reset : Signal dom Bool)
    (s0 : Signal dom Bool)
    : Signal dom (BitVec 3) :=
  Signal.loop fun (state : Signal dom (BitVec 3)) =>
    let isA2 : Signal dom Bool := state === Signal.pure stA2
    let nextFromA2 : Signal dom (BitVec 3) :=
      Signal.mux s0 (Signal.pure stB1) (Signal.pure stA2)
    let nextState : Signal dom (BitVec 3) :=
      Signal.mux isA2 nextFromA2 (Signal.pure stA2)
    let nextWithReset : Signal dom (BitVec 3) :=
      Signal.mux reset (Signal.pure stA2) nextState
    Signal.register stA2 nextWithReset

#synthesizeVerilog testFSM7
