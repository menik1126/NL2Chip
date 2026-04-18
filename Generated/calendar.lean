import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Perpetual calendar with seconds, minutes, and hours counters -/
def calendar {dom : DomainConfig}
    (rst : Signal dom Bool) : Signal dom (BitVec 6 × BitVec 6 × BitVec 6) :=
  let state := Signal.loop fun s =>
    -- Extract individual counters from 18-bit state (6+6+6)
    let secs := Signal.map (fun x => x.extractLsb' 0 6) s
    let mins := Signal.map (fun x => x.extractLsb' 6 6) s
    let hours := Signal.map (fun x => x.extractLsb' 12 6) s
    
    -- Seconds logic: 0-59, wraps to 0
    let secsAt59 := secs === 59#6
    let nextSecs := Signal.mux rst (Signal.pure 0#6)
      (Signal.mux secsAt59 (Signal.pure 0#6) (secs + 1#6))
    
    -- Minutes logic: increments when secs=59, wraps at 59
    let minsAt59 := mins === 59#6
    let bothAt59 := minsAt59 &&& secsAt59
    let nextMins := Signal.mux rst (Signal.pure 0#6)
      (Signal.mux bothAt59 (Signal.pure 0#6)
        (Signal.mux secsAt59 (mins + 1#6) mins))
    
    -- Hours logic: increments when mins=59 && secs=59, wraps at 23
    let hoursAt23 := hours === 23#6
    let allAtMax := hoursAt23 &&& bothAt59
    let nextHours := Signal.mux rst (Signal.pure 0#6)
      (Signal.mux allAtMax (Signal.pure 0#6)
        (Signal.mux bothAt59 (hours + 1#6) hours))
    
    -- Concatenate back into 18-bit state
    let nextState := nextHours ++ nextMins ++ nextSecs
    Signal.register 0#18 nextState
  
  -- Extract and bundle outputs
  let secs := Signal.map (fun x => x.extractLsb' 0 6) state
  let mins := Signal.map (fun x => x.extractLsb' 6 6) state
  let hours := Signal.map (fun x => x.extractLsb' 12 6) state
  bundle2 hours (bundle2 mins secs)

#synthesizeVerilog calendar
