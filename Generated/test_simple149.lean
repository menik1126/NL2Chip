import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

private abbrev stA : BitVec 3 := 0#3
private abbrev stB : BitVec 3 := 1#3

def testFSM {dom : DomainConfig}
    (reset : Signal dom Bool)
    (inp : Signal dom Bool)
    : Signal dom (BitVec 4) :=
  let state : Signal dom (BitVec 3) :=
    Signal.loop fun (state : Signal dom (BitVec 3)) =>
      let isA : Signal dom Bool := state === Signal.pure stA
      let next : Signal dom (BitVec 3) :=
        Signal.mux isA (Signal.pure stB) (Signal.pure stA)
      let nextWithReset : Signal dom (BitVec 3) :=
        Signal.mux reset (Signal.pure stA) next
      Signal.register stA nextWithReset
  let isA : Signal dom Bool := state === Signal.pure stA
  let out : Signal dom (BitVec 4) :=
    Signal.mux isA (Signal.pure 15#4) (Signal.pure 0#4)
  out

#synthesizeVerilog testFSM
