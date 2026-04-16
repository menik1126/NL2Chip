import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

private abbrev stA : BitVec 2 := 0#2
private abbrev stB : BitVec 2 := 1#2

def test_simple_fsm3 {dom : DomainConfig}
    (r : Signal dom (BitVec 3))
    : Signal dom (BitVec 3) :=
  let r0 := Signal.map (fun x => x.getLsb 0) r
  let state := Signal.loop fun (st : Signal dom (BitVec 2)) =>
    let isA := st === Signal.pure stA
    let next := Signal.mux isA
      (Signal.mux r0 (Signal.pure stB) (Signal.pure stA))
      (Signal.mux r0 (Signal.pure stB) (Signal.pure stA))
    Signal.register stA next
  let outIsB := state === Signal.pure stB
  Signal.mux outIsB (Signal.pure 1#3) (Signal.pure 0#3)

#synthesizeVerilog test_simple_fsm3
