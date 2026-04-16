import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

def prob148_simple {dom : DomainConfig}
    (resetn : Signal dom Bool)
    : Signal dom (BitVec 2) :=
  Signal.loop fun (state : Signal dom (BitVec 2)) =>
    let reset_active := ~~~resetn
    let nextWithReset := Signal.mux reset_active (Signal.pure 0#2) state
    Signal.register 0#2 nextWithReset

#synthesizeVerilog prob148_simple
