import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

private abbrev st0 : BitVec 2 := 0#2
private abbrev st1 : BitVec 2 := 1#2
private abbrev st2 : BitVec 2 := 2#2

def three_state_fsm {dom : DomainConfig}
    (inp : Signal dom Bool)
    : Signal dom (BitVec 2) :=
  Signal.loop fun (state : Signal dom (BitVec 2)) =>
    let is0 := state === Signal.pure st0
    let is1 := state === Signal.pure st1
    let is2 := state === Signal.pure st2
    
    let next0 := Signal.mux inp (Signal.pure st1) (Signal.pure st0)
    let next1 := Signal.mux inp (Signal.pure st2) (Signal.pure st0)
    let next2 := Signal.mux inp (Signal.pure st0) (Signal.pure st1)
    
    let next := Signal.mux is0 next0 (
      Signal.mux is1 next1 (
        Signal.mux is2 next2 (Signal.pure st0)
      )
    )
    
    Signal.register st0 next

#synthesizeVerilog three_state_fsm
