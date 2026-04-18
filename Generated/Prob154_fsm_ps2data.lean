import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

private abbrev stBYTE1 : BitVec 2 := 0#2
private abbrev stBYTE2 : BitVec 2 := 1#2
private abbrev stBYTE3 : BitVec 2 := 2#2
private abbrev stDONE  : BitVec 2 := 3#2

/-- FSM that searches for message boundaries in a byte stream.
    Discards bytes until in[3]=1, then collects 3 bytes and signals done. -/
def prob154_fsm_ps2data {dom : DomainConfig}
    (reset : Signal dom Bool)
    (inp : Signal dom (BitVec 8))
    : Signal dom (BitVec 1 × BitVec 24) :=
  -- Extract bit 3 of input
  let bit3_masked := inp &&& (8#8 : BitVec 8)
  let in3 := bit3_masked === (8#8 : BitVec 8)
  
  -- Combined state and byte register loop
  let state_and_bytes := Signal.loop fun (sb : Signal dom (BitVec 26)) =>
    -- State is bits [25:24], bytes are bits [23:0]
    let state := Signal.map (fun x => x.extractLsb 25 24) sb
    let bytes := Signal.map (fun x => x.extractLsb 23 0) sb
    
    -- Next state logic
    let isBYTE1 := state === stBYTE1
    let isBYTE2 := state === stBYTE2
    let isBYTE3 := state === stBYTE3
    
    let nextState := 
      Signal.mux isBYTE1
        (Signal.mux in3 stBYTE2 stBYTE1)
        (Signal.mux isBYTE2
          stBYTE3
          (Signal.mux isBYTE3
            stDONE
            (Signal.mux in3 stBYTE2 stBYTE1)))
    
    -- Apply reset
    let nextStateWithReset := Signal.mux reset stBYTE1 nextState
    
    -- Shift register for bytes: {bytes[15:0], in}
    let nextBytes := Signal.map (fun pair : BitVec 24 × BitVec 8 => 
      let old := pair.fst
      let new := pair.snd
      let lower16 := old.extractLsb 15 0
      (lower16 ++ new : BitVec 24)
    ) (bundle2 bytes inp)
    
    -- Combine state and bytes into a single BitVec
    let combined := Signal.map (fun pair : BitVec 2 × BitVec 24 =>
      let s := pair.fst
      let b := pair.snd
      (s ++ b : BitVec 26)
    ) (bundle2 nextStateWithReset nextBytes)
    
    -- Register combined state and bytes
    Signal.register 0#26 combined
  
  -- Extract state and bytes from the loop output
  let state := Signal.map (fun x => x.extractLsb 25 24) state_and_bytes
  let out_bytes_r := Signal.map (fun x => x.extractLsb 23 0) state_and_bytes
  
  -- Output: done signal
  let isDONE := state === stDONE
  let done := Signal.mux isDONE 1#1 0#1
  
  -- out_bytes just outputs the register value (don't care when not done)
  bundle2 done out_bytes_r

#synthesizeVerilog prob154_fsm_ps2data
