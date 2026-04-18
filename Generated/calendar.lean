import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

declare_signal_state CalendarState
  | secs  : BitVec 6 := 0#6
  | mins  : BitVec 6 := 0#6
  | hours : BitVec 6 := 0#6

/-- Perpetual calendar with seconds, minutes, and hours counters.
    Counts from 0-59 for seconds and minutes, 0-23 for hours. -/
def calendar {dom : DomainConfig}
    (rst : Signal dom Bool) 
    : Signal dom (BitVec 6 × (BitVec 6 × BitVec 6)) :=
  let loopState := Signal.loop fun state =>
    let secs := CalendarState.secs state
    let mins := CalendarState.mins state
    let hours := CalendarState.hours state
    
    -- Seconds logic: wrap at 59
    let secsAt59 := secs === 59#6
    let nextSecs := Signal.mux rst 
      (Signal.pure 0#6)
      (Signal.mux secsAt59 (Signal.pure 0#6) (secs + 1#6))
    
    -- Minutes logic: increment when secs=59, wrap at 59
    let minsAt59 := mins === 59#6
    let bothAt59 := minsAt59 &&& secsAt59
    let nextMins := Signal.mux rst
      (Signal.pure 0#6)
      (Signal.mux bothAt59 
        (Signal.pure 0#6)
        (Signal.mux secsAt59 (mins + 1#6) mins))
    
    -- Hours logic: increment when mins=59 && secs=59, wrap at 23
    let hoursAt23 := hours === 23#6
    let allAtMax := hoursAt23 &&& bothAt59
    let nextHours := Signal.mux rst
      (Signal.pure 0#6)
      (Signal.mux allAtMax
        (Signal.pure 0#6)
        (Signal.mux bothAt59 (hours + 1#6) hours))
    
    bundleAll! [
      Signal.register 0#6 nextSecs,
      Signal.register 0#6 nextMins,
      Signal.register 0#6 nextHours
    ]
  
  let secs := CalendarState.secs loopState
  let mins := CalendarState.mins loopState
  let hours := CalendarState.hours loopState
  bundle2 hours (bundle2 mins secs)

#synthesizeVerilog calendar
