import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Linear Feedback Shift Register: 4-bit LFSR with feedback from bits 3 and 2. -/
def LFSR {dom : DomainConfig}
    (rst : Signal dom Bool) : Signal dom (BitVec 4) :=
  Signal.loop fun (out : Signal dom (BitVec 4)) =>
    let feedback := ~~~((out >>> 3#4) ^^^ (out >>> 2#4))
    let shifted := (out <<< 1#4) ||| (feedback &&& 1#4)
    let nextVal := Signal.mux rst (Signal.pure 0#4) shifted
    Signal.register 0#4 nextVal

#synthesizeVerilog LFSR
