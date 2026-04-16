import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def prob148_test {dom : DomainConfig}
    (resetn : Signal dom Bool)
    (r : Signal dom (BitVec 3))
    : Signal dom (BitVec 2) :=
  Signal.loop fun (state : Signal dom (BitVec 2)) =>
    let r0 := Signal.map (fun x => x.getLsb 0) r
    let r1 := Signal.map (fun x => x.getLsb 1) r
    let r2 := Signal.map (fun x => x.getLsb 2) r
    let isA := state === (Signal.pure 0#2)
    let isB := state === (Signal.pure 1#2)
    let isC := state === (Signal.pure 2#2)
    let nextFromA := 
      Signal.mux r0 (Signal.pure 1#2)
        (Signal.mux r1 (Signal.pure 2#2)
          (Signal.mux r2 (Signal.pure 3#2) (Signal.pure 0#2)))
    let nextFromB := Signal.mux r0 (Signal.pure 1#2) (Signal.pure 0#2)
    let nextFromC := Signal.mux r1 (Signal.pure 2#2) (Signal.pure 0#2)
    let nextFromD := Signal.mux r2 (Signal.pure 3#2) (Signal.pure 0#2)
    let nextState := 
      Signal.mux isA nextFromA
        (Signal.mux isB nextFromB
          (Signal.mux isC nextFromC nextFromD))
    let reset_active := ~~~resetn
    let nextWithReset := Signal.mux reset_active (Signal.pure 0#2) nextState
    Signal.register 0#2 nextWithReset

#synthesizeVerilog prob148_test
