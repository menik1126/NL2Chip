import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- State encoding (4 bits for 10 states)
private abbrev stS    : BitVec 4 := 0#4
private abbrev stS1   : BitVec 4 := 1#4
private abbrev stS11  : BitVec 4 := 2#4
private abbrev stS110 : BitVec 4 := 3#4
private abbrev stB0   : BitVec 4 := 4#4
private abbrev stB1   : BitVec 4 := 5#4
private abbrev stB2   : BitVec 4 := 6#4
private abbrev stB3   : BitVec 4 := 7#4
private abbrev stCount : BitVec 4 := 8#4
private abbrev stWait  : BitVec 4 := 9#4

/-- Fancy timer: detects 1101 pattern, shifts in 4-bit delay, counts (delay+1)*1000 cycles -/
def prob156_review2015_fancytimer {dom : DomainConfig}
    (reset : Signal dom Bool)
    (data : Signal dom Bool)
    (ack : Signal dom Bool)
    : Signal dom (BitVec 4 × (BitVec 1 × BitVec 1)) :=
  -- Combined state: (state, fcount, scount)
  let stateAndCounters := Signal.loop fun combined =>
    let state := Signal.map (fun x => x.1) combined
    let fcount := Signal.map (fun x => x.2.1) combined
    let scount := Signal.map (fun x => x.2.2) combined
    
    -- State checks
    let isS := state === Signal.pure stS
    let isS1 := state === Signal.pure stS1
    let isS11 := state === Signal.pure stS11
    let isS110 := state === Signal.pure stS110
    let isB0 := state === Signal.pure stB0
    let isB1 := state === Signal.pure stB1
    let isB2 := state === Signal.pure stB2
    let isB3 := state === Signal.pure stB3
    let isCount := state === Signal.pure stCount
    
    -- Next state logic
    let next_state := 
      Signal.mux isS
        (Signal.mux data (Signal.pure stS1) (Signal.pure stS))
        (Signal.mux isS1
          (Signal.mux data (Signal.pure stS11) (Signal.pure stS))
          (Signal.mux isS11
            (Signal.mux data (Signal.pure stS11) (Signal.pure stS110))
            (Signal.mux isS110
              (Signal.mux data (Signal.pure stB0) (Signal.pure stS))
              (Signal.mux isB0
                (Signal.pure stB1)
                (Signal.mux isB1
                  (Signal.pure stB2)
                  (Signal.mux isB2
                    (Signal.pure stB3)
                    (Signal.mux isB3
                      (Signal.pure stCount)
                      (Signal.mux isCount
                        (let done_counting := (scount === Signal.pure 0#4) &&& (fcount === Signal.pure 999#10)
                         Signal.mux done_counting (Signal.pure stWait) (Signal.pure stCount))
                        (Signal.mux ack (Signal.pure stS) (Signal.pure stWait))))))))))
    
    -- Shift enable: active in B0-B3 states
    let shift_ena := isB0 ||| isB1 ||| isB2 ||| isB3
    
    -- Counting flag
    let counting := isCount
    
    -- Slow counter (scount) logic
    let data_bit := Signal.mux data (Signal.pure 1#4) (Signal.pure 0#4)
    let scount_shifted := (scount <<< 1#4) ||| data_bit
    let scount_decremented := scount - 1#4
    let scount_next := 
      Signal.mux shift_ena
        scount_shifted
        (Signal.mux (counting &&& (fcount === Signal.pure 999#10))
          scount_decremented
          scount)
    
    -- Fast counter (fcount) logic
    let fcount_next :=
      Signal.mux (~~~counting)
        (Signal.pure 0#10)
        (Signal.mux (fcount === Signal.pure 999#10)
          (Signal.pure 0#10)
          (fcount + 1#10))
    
    -- Apply reset
    let next_state_reset := Signal.mux reset (Signal.pure stS) next_state
    let fcount_next_reset := Signal.mux reset (Signal.pure 0#10) fcount_next
    let scount_next_reset := Signal.mux reset (Signal.pure 0#4) scount_next
    
    -- Register all state
    let reg_state := Signal.register stS next_state_reset
    let reg_fcount := Signal.register 0#10 fcount_next_reset
    let reg_scount := Signal.register 0#4 scount_next_reset
    
    -- Combine into tuple for loop feedback
    let inner := bundle2 reg_fcount reg_scount
    bundle2 reg_state inner
  
  -- Extract registered values
  let reg_state := Signal.map (fun x => x.1) stateAndCounters
  let reg_scount := Signal.map (fun x => x.2.2) stateAndCounters
  
  -- Outputs
  let counting := reg_state === Signal.pure stCount
  let count_out := Signal.mux counting reg_scount (Signal.pure 0#4)
  let counting_out := Signal.mux counting (Signal.pure 1#1) (Signal.pure 0#1)
  let done_out := Signal.mux (reg_state === Signal.pure stWait) (Signal.pure 1#1) (Signal.pure 0#1)
  
  let flags := bundle2 counting_out done_out
  bundle2 count_out flags

#synthesizeVerilog prob156_review2015_fancytimer
