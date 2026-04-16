import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding: state IS the output (one-hot for grants)
private abbrev stA : BitVec 3 := 0b000#3  -- No grant
private abbrev stB : BitVec 3 := 0b001#3  -- Grant to device 0
private abbrev stC : BitVec 3 := 0b010#3  -- Grant to device 1
private abbrev stD : BitVec 3 := 0b100#3  -- Grant to device 2

def prob148_2013_q2afsm {dom : DomainConfig}
    (resetn : Signal dom Bool)
    (r : Signal dom (BitVec 3))
    : Signal dom (BitVec 3) :=
  -- State register via Signal.loop - state IS the output!
  Signal.loop fun (st : Signal dom (BitVec 3)) =>
    -- Extract request bits
    let r0 : Signal dom Bool := (r &&& (Signal.pure (1#3 : BitVec 3) : Signal dom (BitVec 3))) === (Signal.pure 1#3)
    let r1 : Signal dom Bool := ((r >>> (1#3 : BitVec 3)) &&& (Signal.pure (1#3 : BitVec 3) : Signal dom (BitVec 3))) === (Signal.pure 1#3)
    let r2 : Signal dom Bool := ((r >>> (2#3 : BitVec 3)) &&& (Signal.pure (1#3 : BitVec 3) : Signal dom (BitVec 3))) === (Signal.pure 1#3)
    
    -- Check current state
    let isA := st === (Signal.pure stA)
    let isB := st === (Signal.pure stB)
    let isC := st === (Signal.pure stC)
    
    -- Next state logic
    let nextState := 
      Signal.mux isA 
        (Signal.mux r0 (Signal.pure stB)
          (Signal.mux r1 (Signal.pure stC)
            (Signal.mux r2 (Signal.pure stD) (Signal.pure stA))))
        (Signal.mux isB 
          (Signal.mux r0 (Signal.pure stB) (Signal.pure stA))
          (Signal.mux isC 
            (Signal.mux r1 (Signal.pure stC) (Signal.pure stA))
            (Signal.mux r2 (Signal.pure stD) (Signal.pure stA))))
    
    -- Apply active-low synchronous reset
    Signal.register stA (Signal.mux (~~~resetn) (Signal.pure stA) nextState)

#synthesizeVerilog prob148_2013_q2afsm
