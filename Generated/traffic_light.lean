import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding for traffic light FSM
private abbrev stIdle : BitVec 2 := 0#2
private abbrev stRed : BitVec 2 := 1#2
private abbrev stYellow : BitVec 2 := 2#2
private abbrev stGreen : BitVec 2 := 3#2

/-- Traffic light controller with pedestrian button.
    Implements a traffic light FSM with red (10 cycles), yellow (5 cycles), green (60 cycles).
    When pass_request is pressed during green with >10 cycles remaining, shortens green to 10 cycles.
    Outputs: (clock[7:0], red, yellow, green) -/
def traffic_light {dom : DomainConfig}
    (rst_n : Signal dom Bool)
    (pass_request : Signal dom Bool)
    : Signal dom (BitVec 8 × (BitVec 1 × (BitVec 1 × BitVec 1))) :=
  -- State machine and counter - return (state, cnt, red, yellow, green)
  let result := Signal.loop fun (sc : Signal dom (BitVec 2 × (BitVec 8 × (BitVec 1 × (BitVec 1 × BitVec 1))))) =>
    let state := Signal.map (fun x => x.1) sc
    let cnt := Signal.map (fun x => x.2.1) sc
    
    -- State transition logic
    let atThreshold := cnt === 3#8
    let nextState :=
      hw_cond (Signal.pure stIdle)
        | state === Signal.pure stIdle => Signal.pure stRed
        | (state === Signal.pure stRed) &&& atThreshold => Signal.pure stGreen
        | (state === Signal.pure stYellow) &&& atThreshold => Signal.pure stRed
        | (state === Signal.pure stGreen) &&& atThreshold => Signal.pure stYellow
        | Signal.pure True => state
    
    -- Check if green and cnt > 10
    let green := state === Signal.pure stGreen
    -- cnt > 10 means cnt >= 11, check by excluding 0..10
    let cntGe11 := ~~~(cnt === 0#8) &&& ~~~(cnt === 1#8) &&& ~~~(cnt === 2#8) &&& ~~~(cnt === 3#8) &&& ~~~(cnt === 4#8) &&& ~~~(cnt === 5#8) &&& ~~~(cnt === 6#8) &&& ~~~(cnt === 7#8) &&& ~~~(cnt === 8#8) &&& ~~~(cnt === 9#8) &&& ~~~(cnt === 10#8)
    let passReqActive := pass_request &&& green &&& cntGe11
    
    -- Detect state transitions
    let notGreen := ~~~green
    let nextIsGreen := nextState === Signal.pure stGreen
    let enteringGreen := nextIsGreen &&& notGreen
    
    let notYellow := ~~~(state === Signal.pure stYellow)
    let nextIsYellow := nextState === Signal.pure stYellow
    let enteringYellow := nextIsYellow &&& notYellow
    
    let notRed := ~~~(state === Signal.pure stRed)
    let nextIsRed := nextState === Signal.pure stRed
    let enteringRed := nextIsRed &&& notRed
    
    let nextCnt :=
      hw_cond (Signal.pure 0#8)
        | ~~~rst_n => Signal.pure 10#8
        | passReqActive => Signal.pure 10#8
        | enteringGreen => Signal.pure 60#8
        | enteringYellow => Signal.pure 5#8
        | enteringRed => Signal.pure 10#8
        | Signal.pure True => cnt - 1#8
    
    -- Apply reset
    let nextStateWithReset := Signal.mux rst_n nextState (Signal.pure stIdle)
    
    -- Compute next light outputs based on next state
    let nextRed := Signal.mux (nextStateWithReset === Signal.pure stRed) (Signal.pure 1#1) (Signal.pure 0#1)
    let nextYellow := Signal.mux (nextStateWithReset === Signal.pure stYellow) (Signal.pure 1#1) (Signal.pure 0#1)
    let nextGreen := Signal.mux (nextStateWithReset === Signal.pure stGreen) (Signal.pure 1#1) (Signal.pure 0#1)
    
    -- Register separately
    let regState := Signal.register stIdle nextStateWithReset
    let regCnt := Signal.register 10#8 nextCnt
    let regRed := Signal.register 0#1 nextRed
    let regYellow := Signal.register 0#1 nextYellow
    let regGreen := Signal.register 0#1 nextGreen
    
    bundle2 regState (bundle2 regCnt (bundle2 regRed (bundle2 regYellow regGreen)))
  
  -- Extract outputs: (cnt, red, yellow, green)
  let cnt := Signal.map (fun x => x.2.1) result
  let red := Signal.map (fun x => x.2.2.1) result
  let yellow := Signal.map (fun x => x.2.2.2.1) result
  let green := Signal.map (fun x => x.2.2.2.2) result
  
  bundle2 cnt (bundle2 red (bundle2 yellow green))

#synthesizeVerilog traffic_light
