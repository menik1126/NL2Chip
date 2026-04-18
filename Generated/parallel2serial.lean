import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Parallel-to-serial converter: converts 4-bit parallel input to serial output (MSB first).
    Outputs valid_out=1 when loading new data, then outputs 4 bits serially over 4 cycles. -/
def parallel2serial {dom : DomainConfig}
    (d : Signal dom (BitVec 4))
    : Signal dom (BitVec 1 × BitVec 1) :=
  -- Use 8-bit state: [7:6] = cnt (2 bits), [3:0] = data (4 bits), [5:4] unused
  let state := Signal.loop fun (state : Signal dom (BitVec 8)) =>
    -- Extract cnt (bits [7:6]) and data (bits [3:0])
    let cnt := (state >>> 6#8) &&& 3#8
    let data := state &&& 15#8
    
    -- Check if cnt == 3
    let atMax := cnt === 3#8
    
    -- Next counter value: if cnt==3 then 0 else cnt+1
    let nextCnt := Signal.mux atMax (Signal.pure 0#8) ((cnt + 1#8) &&& 3#8)
    
    -- Next data value: if cnt==3, load d; else rotate left
    let d8 := Signal.map (fun d4 : BitVec 4 => d4.zeroExtend 8) d
    
    -- Rotate left: {data[2:0], data[3]}
    let dataRotated := ((data &&& 7#8) <<< 1#8) ||| ((data >>> 3#8) &&& 1#8)
    
    let nextData := Signal.mux atMax d8 dataRotated
    
    -- Combine into next state: {nextCnt[1:0], 00, nextData[3:0]}
    let nextState := ((nextCnt &&& 3#8) <<< 6#8) ||| (nextData &&& 15#8)
    
    Signal.register 0#8 nextState
  
  -- Extract outputs from state
  let cnt := (state >>> 6#8) &&& 3#8
  let data := state &&& 15#8
  
  -- Outputs: valid_out (1 when cnt==3), dout (data[3])
  let atMax := cnt === 3#8
  let valid := Signal.mux atMax (Signal.pure 1#1) (Signal.pure 0#1)
  
  -- Extract bit 3 of data
  let dout8 := (data >>> 3#8) &&& 1#8
  let dout := Signal.map (BitVec.truncate 1) dout8
  
  bundle2 valid dout

#synthesizeVerilog parallel2serial
