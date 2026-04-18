import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 4-bit shift register with active-low synchronous reset.
    Shifts in from LSB, outputs from MSB (bit 3). -/
def prob060_m2014_q4k {dom : DomainConfig}
    (resetn : Signal dom Bool)
    (input : Signal dom (BitVec 1)) : Signal dom (BitVec 1) :=
  let sr := Signal.loop fun (state : Signal dom (BitVec 4)) =>
    -- Shift left: {sr[2:0], in}
    let shifted := (state <<< 1#4) ||| Signal.map (fun i => i.zeroExtend 4) input
    -- Reset when resetn is low (active-low)
    let nextVal := Signal.mux resetn shifted (Signal.pure 0#4)
    Signal.register 0#4 nextVal
  -- Output bit 3 (MSB) - shift right by 3 and mask to get bit 3
  Signal.map (fun s : BitVec 4 => BitVec.extractLsb 3 3 s) sr

#synthesizeVerilog prob060_m2014_q4k
