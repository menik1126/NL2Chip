import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding (3 bits for 8 states)
private abbrev stA   : BitVec 3 := 0#3
private abbrev stB   : BitVec 3 := 1#3
private abbrev stC   : BitVec 3 := 2#3
private abbrev stS10 : BitVec 3 := 3#3
private abbrev stS11 : BitVec 3 := 4#3
private abbrev stS20 : BitVec 3 := 5#3
private abbrev stS21 : BitVec 3 := 6#3
private abbrev stS22 : BitVec 3 := 7#3

/-- FSM that checks if exactly 2 out of 3 consecutive w values are 1 -/
def prob133_2014_q3fsm {dom : DomainConfig}
    (reset : Signal dom Bool)
    (s : Signal dom Bool)
    (w : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  let state := Signal.loop fun (state : Signal dom (BitVec 3)) =>
    -- Next state logic
    let isA   := state === Signal.pure stA
    let isB   := state === Signal.pure stB
    let isC   := state === Signal.pure stC
    let isS10 := state === Signal.pure stS10
    let isS11 := state === Signal.pure stS11
    let isS20 := state === Signal.pure stS20
    let isS21 := state === Signal.pure stS21
    let isS22 := state === Signal.pure stS22
    
    -- Next state computation using hw_cond
    let nextState := 
      hw_cond (Signal.pure stA)
        | isA   => Signal.mux s (Signal.pure stB) (Signal.pure stA)
        | isB   => Signal.mux w (Signal.pure stS11) (Signal.pure stS10)
        | isC   => Signal.mux w (Signal.pure stS11) (Signal.pure stS10)
        | isS10 => Signal.mux w (Signal.pure stS21) (Signal.pure stS20)
        | isS11 => Signal.mux w (Signal.pure stS22) (Signal.pure stS21)
        | isS20 => Signal.pure stB
        | isS21 => Signal.mux w (Signal.pure stC) (Signal.pure stB)
        | isS22 => Signal.mux w (Signal.pure stB) (Signal.pure stC)
    
    -- Apply reset
    let nextWithReset := Signal.mux reset (Signal.pure stA) nextState
    
    -- Register state
    Signal.register stA nextWithReset
  
  -- Output z = 1 when in state C
  let isC := state === Signal.pure stC
  Signal.mux isC (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob133_2014_q3fsm
