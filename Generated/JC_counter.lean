import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 64-bit Johnson counter with active-low reset.
    Shifts right, prepending 1 if Q[0]==0, else prepending 0. -/
def JC_counter {dom : DomainConfig}
    (rst_n : Signal dom Bool) : Signal dom (BitVec 64) :=
  Signal.loop fun (q : Signal dom (BitVec 64)) =>
    -- Check LSB: if Q[0] == 0, shift in 1; if Q[0] == 1, shift in 0
    let lsb := q &&& 1#64
    let isZero := lsb === 0#64
    -- Shift right by 1
    let shifted := q >>> 1#64
    -- Create MSB bit: 1 at position 63, or 0
    let msbOne := Signal.pure (9223372036854775808#64 : BitVec 64)  -- 2^63
    let msbZero := Signal.pure (0#64 : BitVec 64)
    let msbBit := Signal.mux isZero msbOne msbZero
    -- Combine shifted value with MSB
    let nextVal := shifted ||| msbBit
    -- Active-low reset: when rst_n is low (false), reset to 0
    let nextWithReset := Signal.mux rst_n nextVal (Signal.pure 0#64)
    Signal.register 0#64 nextWithReset

#synthesizeVerilog JC_counter
