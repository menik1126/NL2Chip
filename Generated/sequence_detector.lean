import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding (one-hot)
private abbrev stIDLE : BitVec 5 := 0b00001#5
private abbrev stS1   : BitVec 5 := 0b00010#5
private abbrev stS2   : BitVec 5 := 0b00100#5
private abbrev stS3   : BitVec 5 := 0b01000#5
private abbrev stS4   : BitVec 5 := 0b10000#5

/-- Sequence detector FSM: detects the sequence "1001" -/
def sequence_detector {dom : DomainConfig}
    (reset_n : Signal dom Bool)
    (data_in : Signal dom Bool)
    : Signal dom (BitVec 1) :=
  let state : Signal dom (BitVec 5) :=
    Signal.loop fun (state : Signal dom (BitVec 5)) =>
      -- Check current state
      let isIDLE := state === (Signal.pure stIDLE)
      let isS1   := state === (Signal.pure stS1)
      let isS2   := state === (Signal.pure stS2)
      let isS3   := state === (Signal.pure stS3)
      let isS4   := state === (Signal.pure stS4)
      
      -- Next state logic based on current state and data_in
      -- IDLE: data_in=1 → S1, data_in=0 → IDLE
      let nextFromIDLE := Signal.mux data_in (Signal.pure stS1) (Signal.pure stIDLE)
      
      -- S1: data_in=1 → S1, data_in=0 → S2
      let nextFromS1 := Signal.mux data_in (Signal.pure stS1) (Signal.pure stS2)
      
      -- S2: data_in=1 → S1, data_in=0 → S3
      let nextFromS2 := Signal.mux data_in (Signal.pure stS1) (Signal.pure stS3)
      
      -- S3: data_in=1 → S4, data_in=0 → IDLE
      let nextFromS3 := Signal.mux data_in (Signal.pure stS4) (Signal.pure stIDLE)
      
      -- S4: data_in=1 → S1, data_in=0 → S2
      let nextFromS4 := Signal.mux data_in (Signal.pure stS1) (Signal.pure stS2)
      
      -- Combine all state transitions using priority mux
      let nextState := 
        Signal.mux isIDLE nextFromIDLE
          (Signal.mux isS1 nextFromS1
            (Signal.mux isS2 nextFromS2
              (Signal.mux isS3 nextFromS3
                (Signal.mux isS4 nextFromS4 (Signal.pure stIDLE)))))
      
      -- Apply reset (active low): reset_n=0 → IDLE
      let not_reset_n := ~~~reset_n
      let nextWithReset := Signal.mux not_reset_n (Signal.pure stIDLE) nextState
      
      -- Register with initial value IDLE
      Signal.register stIDLE nextWithReset
  
  -- Output: sequence_detected = 1 when state == S4
  let isS4 := state === (Signal.pure stS4)
  Signal.mux isS4 (Signal.pure 1#1) (Signal.pure 0#1)

#synthesizeVerilog sequence_detector
