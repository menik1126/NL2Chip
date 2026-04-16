import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 4-bit right shift register with async reset, synchronous load (higher priority) and enable. -/
def prob085_shift4 {dom : DomainConfig}
    (areset : Signal dom Bool)
    (load   : Signal dom Bool)
    (ena    : Signal dom Bool)
    (data   : Signal dom (BitVec 4))
    : Signal dom (BitVec 4) :=
  Signal.loop fun (q : Signal dom (BitVec 4)) =>
    -- Right shift: q[3] becomes 0, q[0] is dropped → q >> 1
    let shifted := q >>> 1#4
    -- Priority: areset > load > ena > hold
    -- When ena: shift right (MSB becomes 0)
    let nextEna  := shifted
    -- When load: load data
    let nextLoad := data
    -- When areset: reset to 0 (modeled as sync mux; domain handles async)
    let next :=
      Signal.mux areset (Signal.pure 0#4)
        (Signal.mux load nextLoad
          (Signal.mux ena nextEna q))
    Signal.register 0#4 next

#synthesizeVerilog prob085_shift4
