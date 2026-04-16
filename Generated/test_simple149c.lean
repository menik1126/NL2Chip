import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

private abbrev stA2 : BitVec 3 := 0#3
private abbrev stB1 : BitVec 3 := 1#3
private abbrev stB2 : BitVec 3 := 2#3
private abbrev stC1 : BitVec 3 := 3#3
private abbrev stC2 : BitVec 3 := 4#3
private abbrev stD1 : BitVec 3 := 5#3

def testFSM3 {dom : DomainConfig}
    (reset : Signal dom Bool)
    (s : Signal dom (BitVec 3))
    : Signal dom (BitVec 3) :=
  let s0 : Signal dom Bool := Signal.map (fun x => x.getLsb 0) s
  let s1 : Signal dom Bool := Signal.map (fun x => x.getLsb 1) s
  let s2 : Signal dom Bool := Signal.map (fun x => x.getLsb 2) s
  Signal.loop fun (state : Signal dom (BitVec 3)) =>
    let isA2 : Signal dom Bool := state === Signal.pure stA2
    let isB1 : Signal dom Bool := state === Signal.pure stB1
    let isB2 : Signal dom Bool := state === Signal.pure stB2
    let isC1 : Signal dom Bool := state === Signal.pure stC1
    let isC2 : Signal dom Bool := state === Signal.pure stC2
    let isD1 : Signal dom Bool := state === Signal.pure stD1
    let nextFromA2 : Signal dom (BitVec 3) :=
      Signal.mux s0 (Signal.pure stB1) (Signal.pure stA2)
    let nextFromB1 : Signal dom (BitVec 3) :=
      Signal.mux s1 (Signal.pure stC1) (Signal.mux s0 (Signal.pure stB1) (Signal.pure stA2))
    let nextFromB2 : Signal dom (BitVec 3) :=
      Signal.mux s1 (Signal.pure stC1) (Signal.mux s0 (Signal.pure stB2) (Signal.pure stA2))
    let nextFromC1 : Signal dom (BitVec 3) :=
      Signal.mux s2 (Signal.pure stD1) (Signal.mux s1 (Signal.pure stC1) (Signal.pure stB2))
    let nextFromC2 : Signal dom (BitVec 3) :=
      Signal.mux s2 (Signal.pure stD1) (Signal.mux s1 (Signal.pure stC2) (Signal.pure stB2))
    let nextFromD1 : Signal dom (BitVec 3) :=
      Signal.mux s2 (Signal.pure stD1) (Signal.pure stC2)
    let nextState : Signal dom (BitVec 3) :=
      Signal.mux isA2 nextFromA2 (
      Signal.mux isB1 nextFromB1 (
      Signal.mux isB2 nextFromB2 (
      Signal.mux isC1 nextFromC1 (
      Signal.mux isC2 nextFromC2 (
      Signal.mux isD1 nextFromD1 (Signal.pure stA2))))))
    let nextWithReset : Signal dom (BitVec 3) :=
      Signal.mux reset (Signal.pure stA2) nextState
    Signal.register stA2 nextWithReset

#synthesizeVerilog testFSM3
