import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

private abbrev st0 : BitVec 2 := 0#2
private abbrev st1 : BitVec 2 := 1#2

def simple_fsm {dom : DomainConfig}
    (inp : Signal dom Bool)
    : Signal dom (BitVec 2) :=
  Signal.loop fun (state : Signal dom (BitVec 2)) =>
    let is0 := state === Signal.pure st0
    let next := Signal.mux is0 
      (Signal.mux inp (Signal.pure st1) (Signal.pure st0))
      (Signal.mux inp (Signal.pure st0) (Signal.pure st1))
    Signal.register st0 next

#synthesizeVerilog simple_fsm
