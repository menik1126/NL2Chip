import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 4-bit shift register with async reset, load, and enable.
    Right shift: q[3] becomes 0, q[0] is shifted out.
    Priority: areset > load > ena. -/
def prob085_shift4 {dom : DomainConfig}
    (areset : Signal dom Bool)
    (load : Signal dom Bool)
    (ena : Signal dom Bool)
    (data : Signal dom (BitVec 4))
    : Signal dom (BitVec 4) :=
  Signal.loop fun (q : Signal dom (BitVec 4)) =>
    -- Right shift: q >>> 1
    let shifted := q >>> 1#4
    -- Priority: load > ena > hold
    let nextVal := Signal.mux load data (Signal.mux ena shifted q)
    -- Apply async reset (modeled as sync): areset → 0
    let nextWithReset := Signal.mux areset (Signal.pure 0#4) nextVal
    Signal.register 0#4 nextWithReset

#synthesizeVerilog prob085_shift4
