import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding
private abbrev s0 : BitVec 2 := 0#2  -- initial
private abbrev s1 : BitVec 2 := 1#2  -- saw 0
private abbrev s2 : BitVec 2 := 2#2  -- saw 01
private abbrev s3 : BitVec 2 := 3#2  -- saw 010 (pulse complete)

/-- Pulse detector: detects 0→1→0 pattern (3 cycles), outputs 1 on pulse completion -/
def pulse_detect {dom : DomainConfig}
    (rst_n : Signal dom Bool)
    (data_in : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  -- State machine
  let state := Signal.loop fun (st : Signal dom (BitVec 2)) =>
    -- Next state logic based on current state and input
    let is_s0 := st === Signal.pure s0
    let is_s1 := st === Signal.pure s1
    let is_s2 := st === Signal.pure s2
    let is_s3 := st === Signal.pure s3
    
    -- State transitions:
    -- s0: data_in=0 → s1, data_in=1 → s0
    -- s1: data_in=1 → s2, data_in=0 → s1
    -- s2: data_in=0 → s3, data_in=1 → s0
    -- s3: data_in=1 → s2, data_in=0 → s1
    
    let next_from_s0 := Signal.mux data_in (Signal.pure s0) (Signal.pure s1)
    let next_from_s1 := Signal.mux data_in (Signal.pure s2) (Signal.pure s1)
    let next_from_s2 := Signal.mux data_in (Signal.pure s0) (Signal.pure s3)
    let next_from_s3 := Signal.mux data_in (Signal.pure s2) (Signal.pure s1)
    
    -- Select next state based on current state
    let next_state := 
      hw_cond (Signal.pure s0)
        | is_s0 => next_from_s0
        | is_s1 => next_from_s1
        | is_s2 => next_from_s2
        | is_s3 => next_from_s3
    
    -- Apply reset (active low): ~rst_n → s0
    let reset_active := ~~~rst_n
    let next_with_reset := Signal.mux reset_active (Signal.pure s0) next_state
    
    -- Register the state
    Signal.register s0 next_with_reset
  
  -- Output logic: data_out = 1 when in state s2 and data_in = 0
  let is_s2 := state === Signal.pure s2
  let data_in_is_0 := ~~~data_in
  let output_cond := is_s2 &&& data_in_is_0
  Signal.mux output_cond (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog pulse_detect
