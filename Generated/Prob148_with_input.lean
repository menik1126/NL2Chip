import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def prob148_with_input {dom : DomainConfig}
    (resetn : Signal dom Bool)
    (r : Signal dom (BitVec 3))
    : Signal dom (BitVec 2) :=
  Signal.loop fun (state : Signal dom (BitVec 2)) =>
    let r0 := Signal.map (fun x => x.getLsb 0) r
    let nextState := Signal.mux r0 (Signal.pure 1#2) (Signal.pure 0#2)
    let reset_active := ~~~resetn
    let nextWithReset := Signal.mux reset_active (Signal.pure 0#2) nextState
    Signal.register 0#2 nextWithReset

#synthesizeVerilog prob148_with_input
