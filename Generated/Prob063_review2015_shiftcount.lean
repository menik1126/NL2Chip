import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Four-bit shift register / down counter.
    When shift_ena=1: shift data in MSB-first (q <= {q[2:0], data}).
    When count_ena=1: decrement q by 1.
    Both controls are never asserted simultaneously. -/
def prob063_review2015_shiftcount {dom : DomainConfig}
    (shift_ena : Signal dom Bool)
    (count_ena : Signal dom Bool)
    (data : Signal dom Bool) : Signal dom (BitVec 4) :=
  Signal.loop fun (q : Signal dom (BitVec 4)) =>
    -- Convert Bool data signal to a 4-bit value (0 or 1) for insertion at LSB
    let dataBit : Signal dom (BitVec 4) := Signal.mux data (Signal.pure 1#4) (Signal.pure 0#4)
    -- Shift left by 1 and insert data at LSB: {q[2:0], data}
    let shifted : Signal dom (BitVec 4) := (q <<< 1#4) ||| dataBit
    -- Decrement
    let decremented : Signal dom (BitVec 4) := q - 1#4
    -- Select: shift_ena has priority, then count_ena, else hold
    let next := Signal.mux shift_ena shifted
                  (Signal.mux count_ena decremented q)
    Signal.register 0#4 next

#synthesizeVerilog prob063_review2015_shiftcount
