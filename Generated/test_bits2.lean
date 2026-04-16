import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

private abbrev stA : BitVec 2 := 0#2
private abbrev stB : BitVec 2 := 1#2

def test_bits2 {dom : DomainConfig}
    (resetn : Signal dom Bool)
    (r : Signal dom (BitVec 3))
    : Signal dom (BitVec 3) :=
  let r0 : Signal dom (BitVec 1) := Signal.map (fun x => BitVec.extractLsb' 0 1 x) r
  let r0b : Signal dom Bool := r0 === Signal.pure 1#1
  let state : Signal dom (BitVec 2) :=
    Signal.loop fun (state : Signal dom (BitVec 2)) =>
      let next := Signal.mux r0b (Signal.pure stB) (Signal.pure stA)
      let nextWithReset := Signal.mux resetn next (Signal.pure stA)
      Signal.register stA nextWithReset
  let isB_out : Signal dom Bool := state === Signal.pure stB
  Signal.mux isB_out (Signal.pure 1#3) (Signal.pure 0#3)

#synthesizeVerilog test_bits2
