import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

private abbrev st0 : BitVec 3 := 0#3
private abbrev st1 : BitVec 3 := 1#3
private abbrev st2 : BitVec 3 := 2#3
private abbrev st3 : BitVec 3 := 3#3
private abbrev st4 : BitVec 3 := 4#3
private abbrev st5 : BitVec 3 := 5#3

def six_state_fsm_reset {dom : DomainConfig}
    (reset : Signal dom Bool)
    (s : Signal dom (BitVec 3))
    : Signal dom (BitVec 3) :=
  Signal.loop fun (state : Signal dom (BitVec 3)) =>
    let s0 : Signal dom Bool := Signal.map (fun x => x.getLsb 0) s
    let s1 : Signal dom Bool := Signal.map (fun x => x.getLsb 1) s
    let s2 : Signal dom Bool := Signal.map (fun x => x.getLsb 2) s
    
    let is0 := state === Signal.pure st0
    let is1 := state === Signal.pure st1
    let is2 := state === Signal.pure st2
    let is3 := state === Signal.pure st3
    let is4 := state === Signal.pure st4
    let is5 := state === Signal.pure st5
    
    let next0 := Signal.mux s0 (Signal.pure st1) (Signal.pure st0)
    let next1 := Signal.mux s1 (Signal.pure st2) (Signal.pure st0)
    let next2 := Signal.mux s2 (Signal.pure st3) (Signal.pure st0)
    let next3 := Signal.mux s0 (Signal.pure st4) (Signal.pure st0)
    let next4 := Signal.mux s1 (Signal.pure st5) (Signal.pure st0)
    let next5 := Signal.mux s2 (Signal.pure st0) (Signal.pure st1)
    
    let next := Signal.mux is0 next0 (
      Signal.mux is1 next1 (
        Signal.mux is2 next2 (
          Signal.mux is3 next3 (
            Signal.mux is4 next4 (
              Signal.mux is5 next5 (Signal.pure st0)
            )
          )
        )
      )
    )
    
    let nextWithReset := Signal.mux reset (Signal.pure st0) next
    Signal.register st0 nextWithReset

#synthesizeVerilog six_state_fsm_reset
