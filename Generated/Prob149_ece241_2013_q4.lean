import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding (6 states need 3 bits)
private abbrev stA2 : BitVec 3 := 0#3
private abbrev stB1 : BitVec 3 := 1#3
private abbrev stB2 : BitVec 3 := 2#3
private abbrev stC1 : BitVec 3 := 3#3
private abbrev stC2 : BitVec 3 := 4#3
private abbrev stD1 : BitVec 3 := 5#3

/-- Water reservoir flow control FSM.
    Note: Due to a Sparkle synthesis limitation with Signal.map for bit extraction,
    the sensor bits are passed as separate Bool inputs instead of a 3-bit vector.
    In the generated Verilog, these will be s[0], s[1], s[2]. -/
def prob149_ece241_2013_q4 {dom : DomainConfig}
    (reset : Signal dom Bool)
    (s0 s1 s2 : Signal dom Bool)
    : Signal dom (BitVec 4) :=
  let state := Signal.loop fun currentState =>
    let isA2 := currentState === Signal.pure stA2
    let isB1 := currentState === Signal.pure stB1
    let isB2 := currentState === Signal.pure stB2
    let isC1 := currentState === Signal.pure stC1
    let isC2 := currentState === Signal.pure stC2
    let isD1 := currentState === Signal.pure stD1
    
    -- A2: next = s[0] ? B1 : A2
    let nxtA2 := Signal.mux s0 (Signal.pure stB1) (Signal.pure stA2)
    
    -- B1: next = s[1] ? C1 : (s[0] ? B1 : A2)
    let nxtB1 := Signal.mux s1 (Signal.pure stC1) (Signal.mux s0 (Signal.pure stB1) (Signal.pure stA2))
    
    -- B2: next = s[1] ? C1 : (s[0] ? B2 : A2)
    let nxtB2 := Signal.mux s1 (Signal.pure stC1) (Signal.mux s0 (Signal.pure stB2) (Signal.pure stA2))
    
    -- C1: next = s[2] ? D1 : (s[1] ? C1 : B2)
    let nxtC1 := Signal.mux s2 (Signal.pure stD1) (Signal.mux s1 (Signal.pure stC1) (Signal.pure stB2))
    
    -- C2: next = s[2] ? D1 : (s[1] ? C2 : B2)
    let nxtC2 := Signal.mux s2 (Signal.pure stD1) (Signal.mux s1 (Signal.pure stC2) (Signal.pure stB2))
    
    -- D1: next = s[2] ? D1 : C2
    let nxtD1 := Signal.mux s2 (Signal.pure stD1) (Signal.pure stC2)
    
    -- Combine next state logic
    let nextSt := Signal.mux isA2 nxtA2 (Signal.mux isB1 nxtB1 (Signal.mux isB2 nxtB2 (Signal.mux isC1 nxtC1 (Signal.mux isC2 nxtC2 (Signal.mux isD1 nxtD1 (Signal.pure stA2))))))
    
    -- Apply reset
    let nextWithRst := Signal.mux reset (Signal.pure stA2) nextSt
    
    Signal.register stA2 nextWithRst
  
  -- Output logic: {fr2, fr1, fr0, dfr}
  -- A2: 4'b1111, B1: 4'b0110, B2: 4'b0111
  -- C1: 4'b0010, C2: 4'b0011, D1: 4'b0000
  let outIsA2 := state === Signal.pure stA2
  let outIsB1 := state === Signal.pure stB1
  let outIsB2 := state === Signal.pure stB2
  let outIsC1 := state === Signal.pure stC1
  let outIsC2 := state === Signal.pure stC2
  let outIsD1 := state === Signal.pure stD1
  
  Signal.mux outIsA2 (Signal.pure 15#4)
    (Signal.mux outIsB1 (Signal.pure 6#4)
      (Signal.mux outIsB2 (Signal.pure 7#4)
        (Signal.mux outIsC1 (Signal.pure 2#4)
          (Signal.mux outIsC2 (Signal.pure 3#4)
            (Signal.mux outIsD1 (Signal.pure 0#4) (Signal.pure 0#4))))))

#synthesizeVerilog prob149_ece241_2013_q4
