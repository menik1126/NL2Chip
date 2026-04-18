import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Four-bit shift register that also acts as a down counter.
    When shift_ena is high, shifts in data MSB-first.
    When count_ena is high, decrements the value. -/
def prob063_review2015_shiftcount {dom : DomainConfig}
    (shift_ena : Signal dom Bool)
    (count_ena : Signal dom Bool)
    (data : Signal dom (BitVec 1)) : Signal dom (BitVec 4) :=
  Signal.loop fun (q : Signal dom (BitVec 4)) =>
    -- Shift left: {q[2:0], data} = (q << 1) | data
    let dataExtended := Signal.map (fun (d : BitVec 1) => d.zeroExtend 4) data
    let shifted := (q <<< 1#4) ||| dataExtended
    let decremented := q - 1#4
    let nextVal := Signal.mux shift_ena shifted
      (Signal.mux count_ena decremented q)
    Signal.register 0#4 nextVal

#synthesizeVerilog prob063_review2015_shiftcount
