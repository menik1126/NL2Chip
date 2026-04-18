import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

-- Helper: Convert 5-bit binary to Gray code
private def binToGray5 (bin : BitVec 5) : BitVec 5 :=
  bin ^^^ (bin >>> 1#5)

/-- Asynchronous FIFO with Gray code pointers, depth=16, width=8.
    
    This is a simplified single-domain model of an async FIFO that demonstrates
    the key concepts: Gray code pointers for clock domain crossing safety,
    and full/empty detection logic.
    
    State contains write and read Gray code pointers (5 bits each).
    Full condition: wptr == {~rptr[4:3], rptr[2:0]}
    Empty condition: rptr == wptr
    
    Note: This implementation uses a single clock domain and simplified pointer
    increment logic. A true async FIFO would require dual-port RAM with separate
    clocks and two-stage synchronizers for clock domain crossing.
-/
def asyn_fifo {dom : DomainConfig}
    (wrstn : Signal dom Bool)
    (rrstn : Signal dom Bool)
    (winc : Signal dom Bool)
    (rinc : Signal dom Bool)
    (wdata : Signal dom (BitVec 8))
    : Signal dom (Bool × Bool × BitVec 8) :=
  -- State: wptr[4:0] | rptr[4:0] = 10 bits (Gray code pointers)
  let state := Signal.loop fun (s : Signal dom (BitVec 10)) =>
    let wptr := Signal.map (fun x => x.extractLsb 4 0) s
    let rptr := Signal.map (fun x => x.extractLsb 9 5) s
    
    -- Full detection: wptr == {~rptr[4:3], rptr[2:0]}
    -- This detects when write pointer has wrapped around and caught up to read pointer
    let rptr_flipped := Signal.map (fun r => 
      let top2 := ~~~(r.extractLsb 4 3)
      let bot3 := r.extractLsb 2 0
      top2 ++ bot3) rptr
    let wfull := wptr === rptr_flipped
    
    -- Empty detection: rptr == wptr
    let rempty := rptr === wptr
    
    -- Write/read enable: only when not full/empty and reset is inactive
    let wen := winc &&& (~~~wfull) &&& wrstn
    let ren := rinc &&& (~~~rempty) &&& rrstn
    
    -- Increment pointers when enabled
    let wptr_next := Signal.mux wen (wptr + 1#5) wptr
    let rptr_next := Signal.mux ren (rptr + 1#5) rptr
    
    -- Pack state: concatenate read and write pointers
    let next_state := (fun w r => r ++ w) <$> wptr_next <*> rptr_next
    
    -- Reset to zero when either reset is active
    let reset_val := Signal.pure 0#10
    let final_state := Signal.mux (wrstn &&& rrstn) next_state reset_val
    
    Signal.register 0#10 final_state
  
  -- Extract outputs from state
  let wptr := Signal.map (fun x => x.extractLsb 4 0) state
  let rptr := Signal.map (fun x => x.extractLsb 9 5) state
  
  -- Recompute full/empty flags for output
  let rptr_flipped := Signal.map (fun r => 
    let top2 := ~~~(r.extractLsb 4 3)
    let bot3 := r.extractLsb 2 0
    top2 ++ bot3) rptr
  let wfull := wptr === rptr_flipped
  let rempty := rptr === wptr
  
  -- Read data output (simplified: pass through write data)
  -- A full implementation would include dual-port RAM
  let rdata := wdata
  
  -- Bundle outputs: (wfull, rempty, rdata)
  bundle2 wfull (bundle2 rempty rdata)

#synthesizeVerilog asyn_fifo
