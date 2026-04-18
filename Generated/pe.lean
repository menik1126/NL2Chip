import Sparkle
import Sparkle.Compiler.Elab

open Sparkle.Core.Domain
open Sparkle.Core.Signal

/-- Multiplying Accumulator (MAC) for 32-bit integers.
    Accumulates a*b into register c each cycle. Reset clears accumulator. -/
def pe {dom : DomainConfig}
    (rst : Signal dom Bool)
    (a : Signal dom (BitVec 32))
    (b : Signal dom (BitVec 32)) : Signal dom (BitVec 32) :=
  Signal.loop fun (c : Signal dom (BitVec 32)) =>
    let product := a * b
    let nextVal := Signal.mux rst (Signal.pure 0#32) (c + product)
    Signal.register 0#32 nextVal

#synthesizeVerilog pe
