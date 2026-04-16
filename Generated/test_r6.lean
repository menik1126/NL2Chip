import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

private abbrev stA : BitVec 2 := 0#2
private abbrev stB : BitVec 2 := 1#2

def test_r6 {dom : DomainConfig}
    (resetn : Signal dom Bool)
    (r : Signal dom (BitVec 3))
    : Signal dom (BitVec 3) :=
  let state : Signal dom (BitVec 2) :=
    Signal.loop fun (state : Signal dom (BitVec 2)) =>
      let r0 : Signal dom Bool := Signal.map (fun x => x.getLsb 0) r
      let next := Signal.mux r0 (Signal.pure stB) (Signal.pure stA)
      let nextWithReset := Signal.mux resetn next (Signal.pure stA)
      Signal.register stA nextWithReset
  let isB_out : Signal dom Bool := state === Signal.pure stB
  Signal.mux isB_out (Signal.pure 1#3) (Signal.pure 0#3)

#synthesizeVerilog test_r6
