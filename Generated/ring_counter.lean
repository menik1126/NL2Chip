import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- 8-bit ring counter: cycles a single 1 bit through 8 positions. -/
def ring_counter {dom : DomainConfig}
    (reset : Signal dom Bool) : Signal dom (BitVec 8) :=
  Signal.loop fun (state : Signal dom (BitVec 8)) =>
    -- Rotate left: shift left by 1 and OR with bit 7 shifted to bit 0
    let shiftedLeft := state <<< 1#8
    let bit7 := state >>> 7#8
    let rotated := shiftedLeft ||| bit7
    let nextVal := Signal.mux reset (Signal.pure 1#8) rotated
    Signal.register 1#8 nextVal

#synthesizeVerilog ring_counter
