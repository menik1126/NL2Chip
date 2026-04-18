import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding (9 states need 4 bits)
private abbrev stA   : BitVec 4 := 0#4
private abbrev stB   : BitVec 4 := 1#4
private abbrev stS0  : BitVec 4 := 2#4
private abbrev stS1  : BitVec 4 := 3#4
private abbrev stS10 : BitVec 4 := 4#4
private abbrev stG1  : BitVec 4 := 5#4
private abbrev stG2  : BitVec 4 := 6#4
private abbrev stP0  : BitVec 4 := 7#4
private abbrev stP1  : BitVec 4 := 8#4

/-- FSM for motor control with pattern detection and timing constraints -/
def prob139_2013_q2bfsm {dom : DomainConfig}
    (resetn : Signal dom Bool)
    (x : Signal dom Bool)
    (y : Signal dom Bool)
    : Signal dom (BitVec 1 × BitVec 1) :=
  let state := Signal.loop fun (state : Signal dom (BitVec 4)) =>
    -- Next state logic
    let nextState := 
      hw_cond (Signal.pure stA)
        | state === stA   => Signal.pure stB
        | state === stB   => Signal.pure stS0
        | state === stS0  => Signal.mux x (Signal.pure stS1) (Signal.pure stS0)
        | state === stS1  => Signal.mux x (Signal.pure stS1) (Signal.pure stS10)
        | state === stS10 => Signal.mux x (Signal.pure stG1) (Signal.pure stS0)
        | state === stG1  => Signal.mux y (Signal.pure stP1) (Signal.pure stG2)
        | state === stG2  => Signal.mux y (Signal.pure stP1) (Signal.pure stP0)
        | state === stP0  => Signal.pure stP0
        | state === stP1  => Signal.pure stP1
    
    -- Apply reset (active low): ~resetn → A
    let notResetn := ~~~resetn
    let nextWithReset := Signal.mux notResetn (Signal.pure stA) nextState
    
    -- Register with initial value A
    Signal.register stA nextWithReset
  
  -- Output f: state == B
  let f_bool := state === stB
  let f := Signal.mux f_bool (Signal.pure 1#1) (Signal.pure 0#1)
  
  -- Output g: state == G1 || state == G2 || state == P1
  let isG1 := state === stG1
  let isG2 := state === stG2
  let isP1 := state === stP1
  let g_bool := isG1 ||| isG2 ||| isP1
  let g := Signal.mux g_bool (Signal.pure 1#1) (Signal.pure 0#1)
  
  bundle2 f g

#synthesizeVerilog prob139_2013_q2bfsm
