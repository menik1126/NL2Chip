import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- FSM with three flip-flops and combinational logic -/
def prob074_ece241_2014_q4 {dom : DomainConfig}
    (x : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  -- State register: 3 flip-flops stored as 3-bit vector
  let s := Signal.loop fun (state : Signal dom (BitVec 3)) =>
    -- Compute next state: {s[2] ^ x, ~s[1] & x, ~s[0] | x}
    -- Build next state by shifting and ORing bits
    let s0 := state &&& 1#3  -- Extract bit 0
    let s1 := (state >>> 1#3) &&& 1#3  -- Extract bit 1
    let s2 := (state >>> 2#3) &&& 1#3  -- Extract bit 2
    
    -- Extend x to 3 bits for operations
    let x3 := Signal.map (fun xv => xv.zeroExtend 3) x
    
    -- Compute next bits
    let next2 := s2 ^^^ x3
    let next1 := (~~~s1) &&& x3
    let next0 := (~~~s0) ||| x3
    
    -- Combine into 3-bit state: (next2 << 2) | (next1 << 1) | next0
    let nextState := (next2 <<< 2#3) ||| (next1 <<< 1#3) ||| next0
    
    Signal.register 0#3 nextState
  -- Output z is NOR of all three flip-flop outputs (z = ~|s = s == 0)
  let isZero := s === 0#3
  Signal.mux isZero (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog prob074_ece241_2014_q4
