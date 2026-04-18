import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 64-bit arithmetic shift register with synchronous load and variable shift amount -/
def prob115_shift18 {dom : DomainConfig}
    (load : Signal dom Bool)
    (ena : Signal dom Bool)
    (amount : Signal dom (BitVec 2))
    (data : Signal dom (BitVec 64)) : Signal dom (BitVec 64) :=
  Signal.loop fun (q : Signal dom (BitVec 64)) =>
    -- Compute shifted values for each case
    let shiftLeft1 := q <<< 1#64
    let shiftLeft8 := q <<< 8#64
    
    -- For arithmetic right shift, extract MSB and conditionally sign-extend
    -- Extract bit 63 (MSB)
    let msbBit := Signal.map (fun (v : BitVec 64) => v.extractLsb' 63 1) q
    let msbIsOne := msbBit === 1#1
    
    let shiftRight1Base := q >>> 1#64
    let shiftRight1 := Signal.mux msbIsOne 
      (shiftRight1Base ||| (1#64 <<< 63#64))
      shiftRight1Base
    
    let shiftRight8Base := q >>> 8#64
    let shiftRight8 := Signal.mux msbIsOne
      (shiftRight8Base ||| (255#64 <<< 56#64))
      shiftRight8Base
    
    -- Select based on amount (nested mux for 4-way selection)
    let is00 := amount === 0#2
    let is01 := amount === 1#2
    let is10 := amount === 2#2
    -- is11 is the else case
    
    let shifted := Signal.mux is00 shiftLeft1
      (Signal.mux is01 shiftLeft8
        (Signal.mux is10 shiftRight1 shiftRight8))
    
    -- Apply ena and load controls
    let nextVal := Signal.mux load data
      (Signal.mux ena shifted q)
    
    Signal.register 0#64 nextVal

#synthesizeVerilog prob115_shift18
