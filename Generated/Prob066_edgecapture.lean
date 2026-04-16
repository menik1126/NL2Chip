import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 32-bit edge capture: captures 1→0 transitions on each bit.
    Output bit remains 1 until synchronous reset clears it. -/
def prob066_edgecapture {dom : DomainConfig}
    (reset : Signal dom Bool) (inp : Signal dom (BitVec 32))
    : Signal dom (BitVec 32) :=
  -- d_last: register storing the previous value of inp
  let d_last := Signal.register 0#32 inp
  -- detected: bits where inp went from 1 to 0 (d_last=1 AND inp=0)
  let detected := (~~~inp) &&& d_last
  -- out: accumulates captures, cleared on reset
  Signal.loop fun out =>
    let nextOut := Signal.mux reset (Signal.pure 0#32) (out ||| detected)
    Signal.register 0#32 nextOut

#synthesizeVerilog prob066_edgecapture
