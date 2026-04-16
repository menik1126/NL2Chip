import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

private abbrev stA : BitVec 2 := 0#2
private abbrev stB : BitVec 2 := 1#2
private abbrev stC : BitVec 2 := 2#2
private abbrev stD : BitVec 2 := 3#2

def test_r {dom : DomainConfig}
    (resetn : Signal dom Bool)
    (r : Signal dom (BitVec 3))
    : Signal dom (BitVec 3) :=
  let r0 : Signal dom Bool := Signal.map (fun x => x.getLsb 0) r
  let r1 : Signal dom Bool := Signal.map (fun x => x.getLsb 1) r
  let r2 : Signal dom Bool := Signal.map (fun x => x.getLsb 2) r
  let state : Signal dom (BitVec 2) :=
    Signal.loop fun (state : Signal dom (BitVec 2)) =>
      let isA := state === Signal.pure stA
      let isB := state === Signal.pure stB
      let isC := state === Signal.pure stC
      let nextFromA :=
        Signal.mux r0 (Signal.pure stB)
          (Signal.mux r1 (Signal.pure stC)
            (Signal.mux r2 (Signal.pure stD) (Signal.pure stA)))
      let nextFromB := Signal.mux r0 (Signal.pure stB) (Signal.pure stA)
      let nextFromC := Signal.mux r1 (Signal.pure stC) (Signal.pure stA)
      let nextFromD := Signal.mux r2 (Signal.pure stD) (Signal.pure stA)
      let nextState :=
        Signal.mux isA nextFromA
          (Signal.mux isB nextFromB
            (Signal.mux isC nextFromC nextFromD))
      let nextWithReset := Signal.mux resetn nextState (Signal.pure stA)
      Signal.register stA nextWithReset
  let isB_out : Signal dom Bool := state === Signal.pure stB
  let isC_out : Signal dom Bool := state === Signal.pure stC
  let isD_out : Signal dom Bool := state === Signal.pure stD
  Signal.mux isD_out (Signal.pure 4#3)
    (Signal.mux isC_out (Signal.pure 2#3)
      (Signal.mux isB_out (Signal.pure 1#3) (Signal.pure 0#3)))

#synthesizeVerilog test_r
